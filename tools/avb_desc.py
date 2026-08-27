#!/usr/bin/env python3
import struct, sys, hashlib

AVB_MAGIC=b'AVB0'

DESC_TYPES={0:'PROPERTY',1:'HASHTREE',2:'HASH',3:'KERNEL_CMDLINE',4:'CHAIN_PARTITION'}

def parse_vbmeta(blob, off, data_all):
    magic=blob[:4]
    if magic!=AVB_MAGIC:
        print("  not AVB0"); return
    (req_maj,req_min)=struct.unpack('>II',blob[4:12])
    (auth_size,aux_size)=struct.unpack('>QQ',blob[12:28])
    (algo,)=struct.unpack('>I',blob[28:32])
    (hash_off,hash_size,sig_off,sig_size,pk_off,pk_size,pkm_off,pkm_size,desc_off,desc_size,rollback)=struct.unpack('>11Q',blob[32:120])
    (flags,)=struct.unpack('>I',blob[120:124])
    rel=blob[128:128+48].split(b'\0')[0]
    total_hdr=256
    print(f"  AVB0@0x{off:x} req{req_maj}.{req_min} algo={algo} flags={flags} rel={rel}")
    print(f"    auth_block={auth_size} aux_block={aux_size} desc_off={desc_off} desc_size={desc_size} rollback={rollback}")
    # descriptors live in aux block, which starts after header(256)+? Actually:
    # authentication block starts at 256, aux block after auth block.
    aux_start=total_hdr+auth_size
    desc_area=blob[aux_start+desc_off: aux_start+desc_off+desc_size]
    p=0
    while p+16<=len(desc_area):
        (dtag,num_bytes_following)=struct.unpack('>QQ',desc_area[p:p+16])
        dt=DESC_TYPES.get(dtag,f'?{dtag}')
        body=desc_area[p+16:p+16+num_bytes_following]
        if dtag==2: # HASH
            (image_size,)=struct.unpack('>Q',body[0:8])
            (hash_algo_name)=body[8:8+32].split(b'\0')[0]
            (partition_name_len,salt_len,digest_len)=struct.unpack('>III',body[40:52])
            (dflags,)=struct.unpack('>I',body[52:56])
            nm=body[64:64+partition_name_len]
            salt=body[64+partition_name_len:64+partition_name_len+salt_len]
            digest=body[64+partition_name_len+salt_len:64+partition_name_len+salt_len+digest_len]
            print(f"    HASH desc: partition={nm} image_size={image_size} algo={hash_algo_name} flags={dflags}")
            print(f"      salt={salt.hex()}")
            print(f"      digest={digest.hex()}")
        elif dtag==4: # CHAIN
            (rollback_loc,)=struct.unpack('>Q',body[0:8])
            (pn_len,pk_len)=struct.unpack('>II',body[8:16])
            nm=body[64:64+pn_len]
            print(f"    CHAIN desc: partition={nm} rollback_loc={rollback_loc} pubkey_len={pk_len}")
        elif dtag==3:
            print(f"    KERNEL_CMDLINE: {body[16:].split(chr(0).encode())[0][:120]}")
        else:
            print(f"    {dt} desc ({num_bytes_following} bytes)")
        p+=16+num_bytes_following
        # 8-byte align
        if p%8: p+=8-(p%8)

def main():
    for f in sys.argv[1:]:
        with open(f,'rb') as fh: data=fh.read()
        print(f"\n### {f} size={len(data)} sha256={hashlib.sha256(data).hexdigest()}")
        i=0
        found=False
        while True:
            j=data.find(AVB_MAGIC,i)
            if j<0: break
            found=True
            parse_vbmeta(data[j:],j,data)
            i=j+1
        if not found: print("  no AVB0 found")

if __name__=='__main__': main()
