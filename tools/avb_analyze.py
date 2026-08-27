#!/usr/bin/env python3
import struct, sys, hashlib

def h(b): return b.hex()

def find_all(data, needle, limit=None):
    out=[]; i=0
    while True:
        j=data.find(needle,i)
        if j<0: break
        out.append(j); i=j+1
        if limit and len(out)>=limit: break
    return out

def decode_android_header(data, name):
    print(f"\n===== ANDROID BOOT HEADER: {name} =====")
    magic=data[:8]
    print("magic:", magic)
    if magic!=b'ANDROID!':
        print("  NOT an Android boot magic at offset 0")
        # scan first 4096 for it
        p=data.find(b'ANDROID!',0,8192)
        print("  ANDROID! first-found offset:", p)
        return None
    # boot_img_hdr v0/v1/v2 common layout
    (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr,
     second_size, second_addr, tags_addr, page_size,
     header_version, os_version) = struct.unpack('<10I', data[8:48])
    name_f=data[48:48+16].split(b'\0')[0]
    cmdline=data[64:64+512].split(b'\0')[0]
    hdr_id=data[576:576+32]
    extra_cmdline=data[608:608+1024].split(b'\0')[0]
    print(f"kernel_size   : {kernel_size} (0x{kernel_size:x})")
    print(f"kernel_addr   : 0x{kernel_addr:x}")
    print(f"ramdisk_size  : {ramdisk_size} (0x{ramdisk_size:x})")
    print(f"ramdisk_addr  : 0x{ramdisk_addr:x}")
    print(f"second_size   : {second_size}")
    print(f"second_addr   : 0x{second_addr:x}")
    print(f"tags_addr     : 0x{tags_addr:x}")
    print(f"page_size     : {page_size}")
    print(f"header_version: {header_version}")
    print(f"os_version    : 0x{os_version:x}")
    print(f"name          : {name_f}")
    print(f"cmdline       : {cmdline}")
    print(f"extra_cmdline : {extra_cmdline}")
    ps=page_size
    n_kernel=(kernel_size+ps-1)//ps
    n_ramdisk=(ramdisk_size+ps-1)//ps
    n_second=(second_size+ps-1)//ps
    hdr_pages=1
    recovery_dtbo_size=0; dtb_size=0
    if header_version>=1:
        (recovery_dtbo_size,)=struct.unpack('<I',data[1632:1636])
        (recovery_dtbo_off,)=struct.unpack('<Q',data[1636:1644])
        (header_size,)=struct.unpack('<I',data[1644:1648])
        print(f"recovery_dtbo_size: {recovery_dtbo_size}")
        print(f"recovery_dtbo_off : 0x{recovery_dtbo_off:x}")
        print(f"header_size       : {header_size}")
    if header_version>=2:
        (dtb_size,)=struct.unpack('<I',data[1648:1652])
        (dtb_addr,)=struct.unpack('<Q',data[1652:1660])
        print(f"dtb_size          : {dtb_size}")
    # compute layout
    off=ps*hdr_pages
    k_off=off
    r_off=k_off+n_kernel*ps
    print(f"\nlayout: kernel@{k_off} (0x{k_off:x}), ramdisk@{r_off} (0x{r_off:x})")
    total_used=r_off+n_ramdisk*ps
    print(f"pages: hdr=1 kernel={n_kernel} ramdisk={n_ramdisk} second={n_second}")
    print(f"declared content end (kernel+ramdisk+second): {total_used} (0x{total_used:x})")
    return dict(kernel_size=kernel_size,page_size=ps,k_off=k_off,r_off=r_off,
                ramdisk_size=ramdisk_size,total_used=total_used,header_version=header_version)

def decode_qualcomm_envelope(data, k_off, kernel_size, name):
    print(f"\n----- Qualcomm kernel envelope @kernel offset {k_off}: {name} -----")
    seg=data[k_off:k_off+64]
    print("first 32 bytes:", seg[:32])
    if seg[:16]==b'UNCOMPRESSED_IMG':
        raw_size=struct.unpack('<I',seg[16:20])[0]
        print(f"UNCOMPRESSED_IMG wrapper found. inner raw Image size = {raw_size} (0x{raw_size:x})")
        inner=data[k_off+20:k_off+20+64]
        print("inner first 8 bytes:", inner[:8], "hex", h(inner[:8]))
        # arm64 Image header magic 'ARM\x64' at offset 56 (0x38): 'ARMd' i.e. 0x644d5241
        arm=data[k_off+20+56:k_off+20+60]
        print("inner arm64 magic@0x38:", arm, "(expect b'ARMd')")
    else:
        # maybe raw arm64 Image
        arm=data[k_off+56:k_off+60]
        print("no UNCOMPRESSED_IMG. arm64 magic@0x38:", arm, "(b'ARMd' if raw Image)")
        # check gzip
        if seg[:2]==b'\x1f\x8b':
            print("gzip magic at kernel start")

# AVB footer
AVB_FOOTER_MAGIC=b'AVBf'
AVB_MAGIC=b'AVB0'
def decode_avb_footer(data, name):
    print(f"\n----- AVB FOOTER search: {name} -----")
    # footer is last 64 bytes of partition typically
    tail=data[-64:]
    print("last 64 bytes:", h(tail))
    if tail[:4]==AVB_FOOTER_MAGIC:
        print("AVBf footer at end-64")
        off=len(data)-64
    else:
        offs=find_all(data,AVB_FOOTER_MAGIC)
        print("AVBf occurrences:", [hex(x) for x in offs])
        if not offs:
            print("NO AVB footer present.")
            return None
        off=offs[-1]
    (ver_maj,ver_min)=struct.unpack('>II',data[off+4:off+12])
    (orig_image_size,)=struct.unpack('>Q',data[off+12:off+20])
    (vbmeta_offset,)=struct.unpack('>Q',data[off+20:off+28])
    (vbmeta_size,)=struct.unpack('>Q',data[off+28:off+36])
    print(f"footer @0x{off:x}: ver {ver_maj}.{ver_min}")
    print(f"  original_image_size: {orig_image_size} (0x{orig_image_size:x})")
    print(f"  vbmeta_offset      : {vbmeta_offset} (0x{vbmeta_offset:x})")
    print(f"  vbmeta_size        : {vbmeta_size}")
    return dict(footer_off=off,orig_image_size=orig_image_size,vbmeta_offset=vbmeta_offset,vbmeta_size=vbmeta_size)

def decode_vbmeta(data, name, base=0):
    print(f"\n----- vbmeta (AVB0) search: {name} -----")
    offs=find_all(data,AVB_MAGIC)
    print("AVB0 occurrences:", [hex(x) for x in offs])
    for off in offs:
        blob=data[off:]
        if len(blob)<256: continue
        # AvbVBMetaImageHeader big-endian
        magic=blob[:4]
        (req_maj,req_min)=struct.unpack('>II',blob[4:12])
        (auth_data_size,aux_data_size)=struct.unpack('>QQ',blob[12:28])
        (algo,)=struct.unpack('>I',blob[28:32])
        # flags at offset 123? Let's parse per spec:
        # offsets: hash_offset(Q)=32.. we go for flags
        # struct: magic[4], req_maj, req_min, auth_block_size Q, aux_block_size Q, algorithm_type I,
        # hash_offset Q, hash_size Q, signature_offset Q, signature_size Q,
        # public_key_offset Q, public_key_size Q, public_key_metadata_offset Q, public_key_metadata_size Q,
        # descriptors_offset Q, descriptors_size Q, rollback_index Q, flags I, ...
        idx=32
        (hash_off,hash_size,sig_off,sig_size,pk_off,pk_size,pkm_off,pkm_size,desc_off,desc_size,rollback)=struct.unpack('>11Q',blob[idx:idx+88])
        idx2=idx+88
        (flags,)=struct.unpack('>I',blob[idx2:idx2+4])
        rel=blob[idx2+8:idx2+8+48].split(b'\0')[0]
        print(f"\n  AVB0 @0x{off:x}: req {req_maj}.{req_min} algo={algo}")
        print(f"    auth_block_size={auth_data_size} aux_block_size={aux_data_size}")
        print(f"    descriptors_off={desc_off} size={desc_size} rollback={rollback}")
        print(f"    FLAGS = {flags}  (3 = DISABLE_VERIFY|DISABLE_HASHTREE)")
        print(f"    release_string: {rel}")
    return offs

def main():
    files=sys.argv[1:]
    for f in files:
        with open(f,'rb') as fh: data=fh.read()
        name=f
        print("\n"+"#"*70)
        print(f"# FILE: {name}  size={len(data)} (0x{len(data):x})  sha256={hashlib.sha256(data).hexdigest()}")
        print("#"*70)
        hdr=decode_android_header(data,name)
        if hdr:
            decode_qualcomm_envelope(data,hdr['k_off'],hdr['kernel_size'],name)
        decode_avb_footer(data,name)
        decode_vbmeta(data,name)

if __name__=='__main__':
    main()
