#!/usr/bin/env python3
"""Normalize the unsigned UKI's COFF timestamp before calculating its signature."""
import os
from pathlib import Path
import sys
import pefile

path = Path(sys.argv[1])
epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '1792454400'))
if not 0 <= epoch <= 0xffffffff:
    raise SystemExit('SOURCE_DATE_EPOCH is outside the PE timestamp range')
pe = pefile.PE(str(path))
if pe.OPTIONAL_HEADER.DATA_DIRECTORY[4].Size:
    raise SystemExit('Refusing to rewrite an already signed image')
pe.FILE_HEADER.TimeDateStamp = epoch
pe.OPTIONAL_HEADER.CheckSum = pe.generate_checksum()
path.write_bytes(pe.write())
