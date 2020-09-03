import argparse
import os
import sys
import yaml
from datetime import datetime

from frugy.__init__ import __version__
from frugy.fru import Fru
from frugy.fru_registry import FruRecordType, rec_enumerate, rec_lookup_by_name


def list_supported_records():
    width = 108
    separator = '#' * width
    lf = f"#".ljust(width-1) + '#'
    print(separator)
    for rec_type in list(FruRecordType):
        rec_list = rec_enumerate(rec_type)
        if len(rec_list) != 0:
            print(lf)
            print(f'# type: {rec_type.name}'.ljust(width-1) + '#')
            print(lf)
            for r in rec_list:
                print(f'# {r.__name__.ljust(33)} {str(r.__doc__).strip()}'.ljust(width-1) + '#')
            print(lf)
            print(separator)

def list_record_schema(rec_name):
    schema = rec_lookup_by_name(rec_name)._schema
    print(f'{"Name".ljust(20)} {"Type".ljust(30)} {"Opt"}')

    for entry in schema:
        e_name = entry[0]
        if e_name.startswith('_'):
            continue
        e_inst = entry[1]
        e_opt = ''
        if e_inst._description == 'int':
            e_args = e_inst.args[0]
            if 'constants' in e_inst.kwargs:
                e_opt = ', '.join(f'{k}={v}' for k, v in e_inst.kwargs['constants'].items())
        elif e_inst._description == 'array':
            e_args = e_inst.args[0].__name__
        else:
            e_args = e_inst.args
            e_opt = e_inst.kwargs

        e_type = f'{e_inst._description.ljust(5)} ({e_args})'
        print(f'{e_name.ljust(20)} {e_type.ljust(30)} {e_opt}')


def writer(fname, content, bin_mode=False):
    if fname != '-':
        with open(fname, 'wb' if bin_mode else 'w') as f:
            f.write(content)
    else:
        if bin_mode:
            os.write(sys.stdout.fileno(), content)
        else:
            sys.stdout.write(content)

def dict_set(d, keys, item):
    if len(keys) > 1:
        key, rest = keys[0], keys[1:]
        if key not in d:
            d[key] = {}
        dict_set(d[key], rest, item)
    else:
        d[keys[0]] = item

def main():
    parser = argparse.ArgumentParser(
        description='FRU Generator YAML'
    )
    parser.add_argument('srcfile',
                        type=str,
                        nargs='?',
                        help='Source file for reading'
    )
    parser.add_argument('-v', '--version',
                        action='version',
                        version='%(prog)s ' + __version__
    )
    parser.add_argument('-o', '--output',
                        type=str,
                        help='output file (derived from input file if not set)'
    )
    parser.add_argument('-w', '--write',
                        action='store_true',
                        help='FRU write mode (convert YAML to FRU image), default'
    )
    parser.add_argument('-r', '--read',
                        action='store_true',
                        help='FRU read mode (convert FRU image to YAML)'
    )
    parser.add_argument('-d', '--dump',
                        action='store_true',
                        help='dump FRU information to stdout (same as -r -o -)'
    )
    parser.add_argument('-e', '--eeprom-size',
                        type=int,
                        help='pad FRU image to match EEPROM size in bytes (only valid in write mode)'
    )
    parser.add_argument('-s', '--set',
                        type=str,
                        action='append',
                        help='set FRU record field to a value (only valid in write mode)'
    )
    parser.add_argument('-t', '--timestamp',
                        action='store_true',
                        help='set BoardInfo.mfg_date_time timestamp to current UTC time (only valid in write mode)'
    )
    parser.add_argument('-l', '--list',
                        type=str,
                        default=None,
                        const='',
                        nargs='?',
                        help='list supported FRU records or schema of specified record'
    )
    args = parser.parse_args()

    if args.list is not None:
        if args.list == '':
            list_supported_records()
        else:
            list_record_schema(args.list)
        sys.exit(0)
    
    if args.srcfile is None:
        parser.print_help(sys.stderr)
        sys.exit(1)

    read_mode = args.read or args.dump
    if args.write and args.read:
        parser.print_help(sys.stderr)
        sys.exit(1)

    if read_mode and (args.eeprom_size is not None or args.set or args.timestamp):
        parser.print_help(sys.stderr)
        sys.exit(1)

    outfile = args.output
    if args.dump:
        if outfile:
            parser.print_help(sys.stderr)
            sys.exit(1)
        outfile = '-'

    basename, ext = os.path.splitext(os.path.basename(args.srcfile))

    if read_mode and ext != '.bin':
        print('Cowardly refusing to read a FRU file not ending with .bin', file=sys.stderr)
        sys.exit(1)
    if not read_mode and ext != '.yml' and ext != '.yaml':
        print('Cowardly refusing to read a YAML file not ending with .yaml or .yml', file=sys.stderr)
        sys.exit(1)

    if not outfile:
        basename, _ = os.path.splitext(os.path.basename(args.srcfile))
        outfile = basename + ('.yml' if read_mode else '.bin')

    fru = Fru()

    if read_mode:
        try:
            fru.load_bin(args.srcfile)
            writer(outfile, fru.dump_yaml())
        except RuntimeError as e:
            print(f'Error while parsing or writing: {e}')
            return False
    else:
        with open(args.srcfile, 'r') as infile:
            fru_dict = yaml.safe_load(infile)

        if args.set is not None:
            for s in args.set:
                k, v = s.split('=')
                dict_set(fru_dict, k.split('.'), v)

        if args.timestamp:
            if 'BoardInfo' in fru_dict:
                fru_dict['BoardInfo']['mfg_date_time'] = datetime.utcnow()
            else:
                print('Error: FRU needs BoardInfo area to carry the timestamp', file=sys.stderr)
                sys.exit(1)

        fru.update(fru_dict)
        img = fru.serialize()
        if args.eeprom_size is not None:
            if len(img) <= args.eeprom_size:
                img += b'\xff' * (args.eeprom_size - len(img))
            else:
                print(f'Error: Image size ({len(img)}) exceeds EEPROM size ({args.eeprom_size})', file=sys.stderr)
                sys.exit(1)
        writer(outfile, img, bin_mode=True)


if __name__ == '__main__':
    main()
