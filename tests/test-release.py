#!/usr/bin/env python3
"""Disposable offline custody/signing tests; never creates deployment keys."""
import hashlib, importlib.util, json, os, shutil, struct, subprocess, sys, uuid
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
SRC=Path('/src');ROOT=Path('/offline');EVIDENCE=Path('/evidence');EVIDENCE.mkdir(exist_ok=True)
tool=SRC/'tools/release.py'
def call(*args,ok=True):
    r=subprocess.run(['python3',str(tool),*map(str,args)],capture_output=True,text=True)
    if (r.returncode==0)!=ok:raise AssertionError((args,r.stdout,r.stderr))
    return r
password=ROOT/'passphrase';password.write_text('disposable-test-only-passphrase-2026');password.chmod(0o600)
checks=[]
def check(s):checks.append(s);print('PASS '+s,flush=True)
for name in ('old','new'):
    call('init','--vault',ROOT/name,'--name',name,'--password-file',password)
    call('verify-vault','--vault',ROOT/name,'--password-file',password)
    call('prepare-trust','--public',ROOT/name/'public','--destination',ROOT/(name+'-build'))
    assert not list((ROOT/(name+'-build')).glob('*.key'))
    pem=(ROOT/name/'firmware.encrypted.pem').read_bytes()
    assert b'ENCRYPTED PRIVATE KEY' in pem
    try:serialization.load_pem_private_key(pem,b'incorrect')
    except ValueError:pass
    else:raise AssertionError('wrong passphrase accepted')
check('encrypted vaults produce public-only, fingerprint-checked build trust')
bad=ROOT/'contaminated';shutil.copytree(ROOT/'old/public',bad);(bad/'private.key').write_bytes(pem)
call('prepare-trust','--public',bad,'--destination',ROOT/'reject',ok=False)
check('public trust preparation rejects private material')
# Exercise the actual external-build key entry point in a disposable /work.
env={**os.environ,'FCR_SIGNING_MODE':'external','FCR_TRUST_DIR':str(ROOT/'new/public')}
subprocess.run(['bash',str(SRC/'boot/tools/make-dev-keys.sh')],env=env,check=True,stdout=subprocess.DEVNULL)
assert sorted(p.name for p in Path('/work/build/keys').iterdir())==['firmware.crt','release-key.h','release.pub','trust.json']
denied=subprocess.run(['bash',str(SRC/'boot/tools/make-dev-keys.sh')],env={**os.environ,'FCR_SIGNING_MODE':'development'},capture_output=True)
assert denied.returncode!=0 and b'Refusing development' in denied.stderr
check('external build entry point accepts public trust and refuses development-key generation in that volume')
unsigned=Path('/boot-work/artifacts/rommon.unsigned.efi');expected=hashlib.sha256(unsigned.read_bytes()).hexdigest()
for name in ('old','new'):
    signed=EVIDENCE/(name+'-rommon.efi')
    call('sign','--vault',ROOT/name,'--password-file',password,'--kind','rommon','--input',unsigned,'--expected-sha256',expected,'--output',signed)
    call('verify','--public',ROOT/name/'public','--kind','rommon','--input',signed,'--unsigned',unsigned)
    shutil.copytree(ROOT/name/'public',EVIDENCE/(name+'-public'))
    call('sign','--vault',ROOT/name,'--password-file',password,'--kind','rommon','--input',unsigned,'--expected-sha256','0'*64,'--output',ROOT/'must-not-exist',ok=False)
call('verify','--public',ROOT/'new/public','--kind','rommon','--input',EVIDENCE/'old-rommon.efi','--unsigned',unsigned,ok=False)
check('offline PE signatures preserve the reviewed image and reject wrong review hashes and keys')
witness=Path('/boot-work/build/kernel-witness/arch/x86/boot/bzImage')
call('sign','--vault',ROOT/'new','--password-file',password,'--kind','system','--input',witness,'--expected-sha256',hashlib.sha256(witness.read_bytes()).hexdigest(),'--output',EVIDENCE/'system.bin')
call('verify','--public',ROOT/'new/public','--kind','system','--input',EVIDENCE/'system.bin','--unsigned',witness)
bad=bytearray((EVIDENCE/'system.bin').read_bytes());bad[200]^=1;(ROOT/'tampered.bin').write_bytes(bad)
call('verify','--public',ROOT/'new/public','--kind','system','--input',ROOT/'tampered.bin','--unsigned',witness,ok=False)
check('system signing and verification enforce both PE and release-envelope authenticity')
for name in ('old','new'):
    binary=Path('/tmp')/('release-verifier-'+name)
    subprocess.run(['gcc','-Os','-DROMMON_HOST_TEST','-I'+str(ROOT/(name+'-build')),'-I/src/service',str(SRC/'boot/rommon/rommon.c'),str(SRC/'boot/rommon/monitor.c'),str(SRC/'service/control.c'),str(SRC/'service/logging.c'),str(SRC/'service/supervisor.c'),'-lcrypto','-o',str(binary)],check=True)
    key=serialization.load_pem_private_key((ROOT/name/'release.encrypted.pem').read_bytes(),password.read_bytes())
    data=b'MZ'+b'offline trust witness'*100
    (ROOT/(name+'.bin')).write_bytes(data+key.sign(b'FractalCR system image v1\n'+data,padding.PKCS1v15(),hashes.SHA256())+b'FCRBIN01')
for trust in ('old','new'):
    for image in ('old','new'):
        r=subprocess.run(['/tmp/release-verifier-'+trust,'--verify',str(ROOT/(image+'.bin'))],capture_output=True)
        assert (r.returncode==0)==(trust==image)
check('real ROMMON verifier accepts its embedded release key and rejects the retired or unrelated key')
rotation=EVIDENCE/'rotation'
call('rotation','--vault',ROOT/'old','--password-file',password,'--new-public',ROOT/'new/public','--output',rotation)
for name,var,append in [('01-db-add-new','db',True),('02-KEK-add-new','KEK',True),('03-dbx-revoke-old','dbx',True),('04-KEK-retire-old','KEK',False),('05-PK-replace','PK',False)]:
    data=(rotation/(name+'.auth')).read_bytes();length=struct.unpack_from('<I',data,16)[0]
    guid=uuid.UUID('8be4df61-93ca-11d2-aa0d-00e098032b8c' if var in ('PK','KEK') else 'd719b2cb-3d3a-4596-a3bc-dad00e67656f').bytes_le
    signed=var.encode('utf-16le')+guid+struct.pack('<I',0x27|(0x40 if append else 0))+data[:16]+data[16+length:]
    content=ROOT/'content';signature=ROOT/'signature';content.write_bytes(signed)
    raw=data[40:16+length]
    def der(tag,payload):
        n=len(payload);encoded=n.to_bytes((n.bit_length()+7)//8,'big')
        return bytes([tag])+(bytes([n]) if n<128 else bytes([0x80|len(encoded)])+encoded)+payload
    # UEFI carries SignedData directly; openssl cms expects ContentInfo around it.
    signature.write_bytes(der(0x30,bytes.fromhex('06092a864886f70d010702')+der(0xa0,raw)))
    r=subprocess.run(['openssl','cms','-verify','-binary','-inform','DER','-in',str(signature),'-content',str(content),'-certfile',str(ROOT/'old/public/firmware.crt'),'-noverify','-out','/dev/null'],capture_output=True)
    assert r.returncode==0,r.stderr
check('all five staged UEFI rotation updates have valid CMS signatures over variable name, attributes, timestamp and payload')
if '--qemu-rotation' in sys.argv:
    spec=importlib.util.spec_from_file_location('uefi_rotation',SRC/'boot/tests/uefi-rotation.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.qualify(ROOT,EVIDENCE,call)
    check('OVMF accepts staged authenticated db/KEK/dbx/PK updates; new ROMMON boots before and after revocation; old ROMMON is rejected')
(EVIDENCE/'result-release.json').write_text(json.dumps(dict(passed=True,checks=checks),indent=2)+'\n')
