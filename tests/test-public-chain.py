#!/usr/bin/env python3
"""Boot a separately built, public-trust-only ROMMON and offline-signed witness."""
import hashlib,json,subprocess,sys
from pathlib import Path
import pexpect
sys.path.insert(0,'/src/boot/tests')
from qemu_process import stop_qemu
root=Path('/evidence');art=root/'artifacts';cert=root/'public/firmware.crt'
variables=root/'vars.fd';flash=root/'flash.img';owner='748e6cc5-19b4-486b-9e22-4e06d330890a'
subprocess.run(['virt-fw-vars','--input','/usr/share/OVMF/OVMF_VARS_4M.fd','--output',str(variables),'--set-pk',owner,str(cert),'--add-kek',owner,str(cert),'--add-db',owner,str(cert),'--secure-boot'],check=True)
subprocess.run(['python3','/src/boot/tools/make-flash.py','--efi',str(art/'rommon.efi'),'--system',str(art/'system.bin'),'--output',str(flash),'--no-tampered-fixture'],check=True)
result=dict(passed=False,trust=json.loads((root/'public/trust.json').read_text()),images={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [art/'rommon.efi',art/'system.bin']})
child=pexpect.spawn('qemu-system-x86_64',['-machine','q35,smm=on','-accel','tcg','-m','512',
    '-global','driver=cfi.pflash01,property=secure,value=on','-drive','if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd',
    '-drive',f'if=pflash,format=raw,unit=1,file={variables}','-drive',f'if=virtio,format=raw,file={flash}',
    '-net','none','-display','none','-serial','stdio','-monitor','none'],encoding='utf-8',timeout=180)
with (root/'public-chain.log').open('w') as log:
    child.logfile_read=log
    try:
        child.expect_exact('Image signature verified.')
        child.expect_exact('FRACTALCR_SYSTEM_BOOT_OK')
        child.expect_exact('Embedded initramfs PID: 1')
        result['passed']=True
        result['checks']=['public-only builder embedded the new release key and kernel trust certificate','separate offline signer produced reviewed ROMMON and witness artifacts','OVMF verified the new firmware signer; ROMMON verified and kexec-booted the new signed witness']
    finally:
        stop_qemu(child)
        (root/'result-public-chain.json').write_text(json.dumps(result,indent=2)+'\n')
print('PASS public-only build -> separate offline signing -> firmware verification -> ROMMON verification -> signed kernel boot',flush=True)
