import bitstruct
from collections import OrderedDict
from enum import Enum
from itertools import zip_longest

_format_version_default = 1

def _sizeAlign(size: int, alignment: int) -> int:
    ''' return number of padding bytes & total length after padding '''
    numPadBytes = -size % alignment
    return numPadBytes, size + numPadBytes


def _grouper(n, iterable, padvalue=None):
    "grouper(3, 'abcdefg', 'x') -> ('a','b','c'), ('d','e','f'), ('g','x','x')"
    return zip_longest(*[iter(iterable)]*n, fillvalue=padvalue)


class FixedField():
    ''' Fixed length field for numbers & bitfields '''

    def __init__(self, format: str, value=None):
        self.format = format
        self.value = value

    def size(self) -> int:
        numBits = bitstruct.calcsize(self.format)
        if numBits % 8 != 0:
            raise RuntimeError("Bitfield not aligned to bytes")
        return numBits // 8

    def serialize(self) -> bytearray:
        if type(self.value) is tuple:
            return bitstruct.pack(self.format + '<', *self.value)
        else:
            return bitstruct.pack(self.format + '<', self.value)

    def deserialize(self, input: bytearray) -> bytearray:
        n = self.size()
        tmp, remainder = input[:n], input[n:]
        result = bitstruct.unpack(self.format + '<', tmp)
        if len(result) > 1:
            self.value = result
        else:
            self.value = result[0]
        return remainder


class StringFmt(Enum):
    BIN = 0b00
    BCD_PLUS = 0b01
    ASCII_6BIT = 0b10
    ASCII_8BIT = 0b11


class StringField():
    ''' Variable length field for strings'''

    def __init__(self, value='', format: StringFmt=StringFmt.ASCII_8BIT):
        self.format = format
        self.value = value

    bcdplus_lookup = {
        '0': 0, '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7,
        '8': 8, '9': 9, ' ': 10, '-': 11, '.': 12
    }
    bcdplus_lookup_rev = {v: k for k, v in bcdplus_lookup.items()}

    def size(self) -> int:
        def size_plain(val: str) -> int:
            return len(val)

        def size_6bit(val: str) -> int:
            _, n = _sizeAlign(len(val), 4)
            return (n // 4) * 3

        def size_bcd_plus(val: str) -> int:
            _, n = _sizeAlign(len(val), 2)
            return n // 2

        size_fn = {
            StringFmt.BIN: size_plain,
            StringFmt.BCD_PLUS: size_bcd_plus,
            StringFmt.ASCII_6BIT: size_6bit,
            StringFmt.ASCII_8BIT: size_plain
        }[self.format]
        return size_fn(self.value) + 1

    def serialize(self) -> bytearray:
        def ser_plain(val: str) -> bytearray:
            return val.encode('utf-8')

        def ser_6bit(val: str) -> bytearray:
            result = b''
            for chunk in _grouper(4, val.upper(), padvalue=' '):
                chunk = list(map(lambda x: ord(x) - 0x20, chunk))
                chunk.reverse()
                tmp = bitstruct.pack('u6'*4, *chunk)
                result += tmp[::-1]
            return result

        def ser_bcd_plus(val: str) -> bytearray:
            result = b''
            for chunk in _grouper(2, val, padvalue=' '):
                chunk = map(lambda x: self.bcdplus_lookup[x], chunk)
                result += bitstruct.pack('u4'*2, *chunk)
            return result

        def ser_type_length(val: str) -> int:
            return bitstruct.pack('u2u6', self.format.value, len(val))

        ser_fn = {
            StringFmt.BIN: ser_plain,
            StringFmt.BCD_PLUS: ser_bcd_plus,
            StringFmt.ASCII_6BIT: ser_6bit,
            StringFmt.ASCII_8BIT: ser_plain
        }[self.format]
        result = ser_fn(self.value)
        return ser_type_length(result) + result

    def deserialize(self, input: bytearray) -> bytearray:
        def deser_plain(val: bytearray) -> str:
            return val.decode('utf-8')

        def deser_6bit(val: bytearray) -> str:
            result = b''
            for chunk in _grouper(3, val, padvalue=' '):
                tmp = bitstruct.unpack('u6'*4, bytearray(reversed(chunk)))
                for x in tmp[::-1]:
                    result += bytes((x + 0x20,))
            return result.decode('utf-8')

        def deser_bcd_plus(val: bytearray) -> str:
            result = ''
            for v in val:
                tmp = bitstruct.unpack('u4'*2, bytes((v,)))
                for x in tmp:
                    result += self.bcdplus_lookup_rev[x]
            return result

        fmt_int, payload_len = bitstruct.unpack('u2u6', input[0:1])
        self.format = StringFmt(fmt_int)
        remainder = input[1:]
        payload, remainder = remainder[:payload_len], remainder[payload_len:]

        deser_fn = {
            StringFmt.BIN: deser_plain,
            StringFmt.BCD_PLUS: deser_bcd_plus,
            StringFmt.ASCII_6BIT: deser_6bit,
            StringFmt.ASCII_8BIT: deser_plain
        }[self.format]
        self.value = deser_fn(payload)

        return remainder


class FruArea:
    ''' Common base class for FRU areas '''
    _schema = None

    def __init__(self, initdict=None):
        self._format_version = FixedField('u4u4', value=(0, _format_version_default))
        self._dict = OrderedDict(self._schema)
        if initdict is not None:
            self.update(initdict)

    # dict interface

    def __getitem__(self, key):
        # check for special accessor
        fname = f'_get_{key}'
        if hasattr(self, fname):
            return getattr(self, fname)()
        else:
            # use generic accessor
            return self._get(key)

    def __setitem__(self, key, value):
        # check for special accessor
        fname = f'_set_{key}'
        if hasattr(self, fname):
            getattr(self, fname)(value)
        else:
            # use generic accessor
            self._set(key, value)

    def __contains__(self, key):
        return hasattr(self, f'_set_{key}') or key in self._dict

    def __repr__(self):
        return repr(self.to_dict())

    def update(self, src):
        for k, v in src.items():
            self[k] = v

    def to_dict(self):
        return {k: self[k] for k in self._dict.keys()}

    # accessors

    def _get(self, key):
        v = self._dict[key].value
        if type(v) is tuple:
            return list(v)
        else:
            return v

    def _set(self, key, value):
        self._dict[key].value = value if type(value) is not list \
                                else tuple(value)

    def _set_area_length(self, val):
        if (val % 8) != 0:
            raise ValueError(f'area length {val} not 64-bit aligned')
        self._set('area_length', val // 8)

    def _get_area_length(self):
        return self._get('area_length') * 8

    def _set_format_version(self, val):
        self._format_version.value = (0, val)

    def _get_format_version(self):
        return self._format_version.value[1]

    # (de)serializing

    def _prologue(self):
        ''' return data to prepend (format version) '''
        return self._format_version.serialize()

    def _epilogue(self, payload):
        ''' return data to append (checksum and padding to 64 bit alignment) '''
        numPadBytes, _ = _sizeAlign(len(payload) + 1, 8)
        result = b'\x00' * numPadBytes
        cksum = (-sum(payload)) & 0xff
        result += cksum.to_bytes(length=1, byteorder='little')
        return result

    def size_payload(self):
        # add one byte for format version
        return sum([v.size() for v in self._dict.values()]) + 1

    def size_total(self):
        # add one byte for checksum
        _, n = _sizeAlign(self.size_payload() + 1, 8)
        return n

    def serialize(self) -> bytearray:
        payload = self._prologue()
        if 'area_length' in self._dict:
            self['area_length'] = self.size_total()
        payload += b''.join([v.serialize() for v in self._dict.values()])
        return payload + self._epilogue(payload)

    def deserialize(self, input: bytearray):
        remainder = self._format_version.deserialize(input)
        for v in self._dict.values():
            remainder = v.deserialize(remainder)
        payload = input[:-len(remainder)]
        ep = self._epilogue(payload)
        vfy, remainder = remainder[:len(ep)], remainder[len(ep):]
        if ep != vfy:
            raise RuntimeError(f'padding or checksum verify error (expected {ep}, received {vfy}')
        return remainder
