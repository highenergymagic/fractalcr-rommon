#!/usr/bin/env python3
"""Exercise native ROMMON TFTP on loopback; requires permission to bind UDP/69."""
import pathlib, socket, struct, subprocess, tempfile, threading

def transfer(driver, mode, payload):
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(('127.0.0.1', 69))
    listener.settimeout(10)
    errors = []
    def server():
        try:
            request, client = listener.recvfrom(1024)
            assert request == b'\0\1image.bin\0octet\0'
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as data:
                data.bind(('127.0.0.1',0)); data.settimeout(5)
                if mode == 'error':
                    data.sendto(b'\0\5\0\1not found\0', client)
                    return
                for index, start in enumerate(range(0,len(payload)+1,512),1):
                    chunk = payload[start:start+512]
                    packet = struct.pack('!HH',3,index & 65535)+chunk
                    if mode == 'duplicate' and index == 1:
                        # Ignore a future block before the first valid block.
                        data.sendto(b'\0\3\0\2wrong',client)
                    data.sendto(packet,client)
                    ack, sender = data.recvfrom(1024)
                    assert sender == client and ack == struct.pack('!HH',4,index & 65535)
                    if mode == 'duplicate' and index == 1:
                        data.sendto(packet,client)
                        assert data.recvfrom(1024)[0] == ack
                    if len(chunk)<512:
                        # Final-block retransmission must be acknowledged during dally.
                        data.sendto(packet,client)
                        assert data.recvfrom(1024)[0] == ack
                        break
        except BaseException as exc:
            errors.append(exc)
        finally:
            listener.close()
    worker=threading.Thread(target=server); worker.start()
    with tempfile.TemporaryDirectory() as directory:
        target=pathlib.Path(directory)/'image.bin'
        result=subprocess.run([driver,'image.bin',str(target)],timeout=130)
        worker.join(12)
        assert not worker.is_alive() and not errors, errors
        if mode=='error':
            assert result.returncode != 0 and not target.exists()
        else:
            assert result.returncode == 0 and target.read_bytes()==payload

if __name__=='__main__':
    import sys
    driver=str(pathlib.Path(sys.argv[1]).resolve())
    for mode,payload in [('empty',b''),('exact',b'x'*1024),('duplicate',bytes(range(256))*5),('error',b''),('wrap',b'w'*(65536*512+17))]:
        transfer(driver,mode,payload)
        print('TFTP_'+mode.upper()+'_OK',flush=True)
    subprocess.run([driver,'ping'],check=True,timeout=10)
