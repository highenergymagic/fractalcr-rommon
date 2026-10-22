"""Apply the generated .auth updates inside disposable OVMF, never host firmware."""
import hashlib, subprocess, sys
from pathlib import Path
import pexpect
sys.path.insert(0,'/src/boot/tests')
from qemu_process import stop_qemu

def qualify(root,evidence,call):
    updates=[('01-db-add-new','db',True),('02-KEK-add-new','KEK',True),('03-dbx-revoke-old','dbx',True),('04-KEK-retire-old','KEK',False),('05-PK-replace','PK',False)]
    for phase,group in [(1,updates[:2]),(2,updates[2:])]:
        source=['#include <efi.h>\n#include <efilib.h>\n']
        for i,(name,var,append) in enumerate(group):
            data=(evidence/'rotation'/(name+'.auth')).read_bytes()
            source.append('static unsigned char auth%d[]={%s};\n'%(i,','.join(str(x) for x in data)))
        source.append('EFI_STATUS efi_main(EFI_HANDLE image,EFI_SYSTEM_TABLE *st){ InitializeLib(image,st); EFI_STATUS status;\n')
        source.append('EFI_GUID global={0x8be4df61,0x93ca,0x11d2,{0xaa,0x0d,0x00,0xe0,0x98,0x03,0x2b,0x8c}};\n')
        source.append('EFI_GUID db={0xd719b2cb,0x3d3a,0x4596,{0xa3,0xbc,0xda,0xd0,0x0e,0x67,0x65,0x6f}};\n')
        for i,(name,var,append) in enumerate(group):
            source.append('status=uefi_call_wrapper(st->RuntimeServices->SetVariable,5,L"%s",&%s,%d,sizeof(auth%d),auth%d); if(EFI_ERROR(status)){Print(L"FCR_ROTATION_FAILED_%s %%r\\r\\n",status);return status;}\n'%(var,'global' if var in ('PK','KEK') else 'db',0x27|(0x40 if append else 0),i,i,name))
        source.append('Print(L"FCR_ROTATION_OK\\r\\n");return EFI_SUCCESS;}\n')
        c=root/f'phase{phase}.c';c.write_text(''.join(source));obj=root/f'phase{phase}.o';so=root/f'phase{phase}.so';unsigned=root/f'phase{phase}.unsigned.efi'
        subprocess.run(['gcc','-I/usr/include/efi','-I/usr/include/efi/x86_64','-fpic','-fshort-wchar','-mno-red-zone','-fno-stack-protector','-DEFI_FUNCTION_WRAPPER','-c',str(c),'-o',str(obj)],check=True)
        subprocess.run(['ld','-nostdlib','-znocombreloc','-T','/usr/lib/elf_x86_64_efi.lds','-shared','-Bsymbolic','/usr/lib/crt0-efi-x86_64.o',str(obj),'-L/usr/lib','-lefi','-lgnuefi','-o',str(so)],check=True)
        subprocess.run(['objcopy','-j','.text','-j','.sdata','-j','.data','-j','.dynamic','-j','.dynsym','-j','.rel','-j','.rela','-j','.reloc','--target=efi-app-x86_64',str(so),str(unsigned)],check=True)
        call('sign','--vault',root/'old','--password-file',root/'passphrase','--kind','rommon','--input',unsigned,'--expected-sha256',hashlib.sha256(unsigned.read_bytes()).hexdigest(),'--output',evidence/f'phase{phase}.efi')
    variables=evidence/'rotation-vars.fd';owner='748e6cc5-19b4-486b-9e22-4e06d330890a';cert=str(root/'old/public/firmware.crt')
    subprocess.run(['virt-fw-vars','--input','/usr/share/OVMF/OVMF_VARS_4M.fd','--output',str(variables),'--set-pk',owner,cert,'--add-kek',owner,cert,'--add-db',owner,cert,'--secure-boot'],check=True,stdout=subprocess.DEVNULL)
    def boot(name,efi,expect):
        flash=evidence/(name+'.img')
        subprocess.run(['python3','/src/boot/tools/make-flash.py','--efi',str(efi),'--system',str(evidence/'system.bin'),'--output',str(flash),'--no-tampered-fixture'],check=True,stdout=subprocess.DEVNULL)
        child=pexpect.spawn('qemu-system-x86_64',['-machine','q35,smm=on','-accel','tcg','-m','512',
            '-global','driver=cfi.pflash01,property=secure,value=on','-drive','if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd',
            '-drive',f'if=pflash,format=raw,unit=1,file={variables}','-drive',f'if=none,id=flash,format=raw,file={flash}',
            '-device','virtio-blk-pci,drive=flash,bootindex=0','-net','none','-display','none','-serial','stdio','-monitor','none'],encoding='utf-8',timeout=90)
        with (evidence/(name+'.log')).open('w') as log:
            child.logfile_read=log
            try:
                found=child.expect([expect,'FCR_ROTATION_FAILED'],timeout=90)
                assert found==0,child.before
                if name=='old-revoked':
                    assert 'FractalCR System Bootstrap' not in child.before
            finally:stop_qemu(child)
    boot('phase1-apply',evidence/'phase1.efi','FCR_ROTATION_OK')
    boot('new-before-revocation',evidence/'new-rommon.efi','FractalCR System Bootstrap')
    boot('phase2-apply',evidence/'phase2.efi','FCR_ROTATION_OK')
    # Match rejection of our first virtio boot target, not a later rejection
    # of the unrelated firmware shell.
    boot('old-revoked',evidence/'old-rommon.efi',r'failed to load Boot0001 "UEFI Misc Device"[^\r\n]*: (?:Access Denied|Security Violation)')
    boot('new-after-revocation',evidence/'new-rommon.efi','FractalCR System Bootstrap')
