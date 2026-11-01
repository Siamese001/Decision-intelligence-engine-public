#!/usr/bin/env python3
"""
tools/apply_directory_upload_patch.py

Applies the arbitrary directory and file upload hardening patch to Antigravity IDE:
1. Dual-Bundle Support: Patches BOTH out/jetskiAgent/main.js and out/vs/workbench/workbench.desktop.main.js.
2. Removes macOS file picker `accept` restrictions (accept: void 0), un-greying .pptx, .vtt, etc.
3. Unblocks dragover and drop in both bundles with recursive folder traversal via webkitGetAsEntry.
4. Expands text extension mappings to include .vtt, .srt, .yaml, .yml, .xml, .sh, .sql, .log.
5. Adds generic binary arrayBuffer fallback (application/octet-stream) for .pptx, .docx, .xlsx.
6. Expands batch attachment ceiling (50 items) and file limits (100MB).
7. Adds dedicated folder upload picker (webkitdirectory) and action button.
8. Updates product.json checksums for all patched files to maintain integrity parity.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(os.path.expanduser("~/Applications/Antigravity IDE/Antigravity IDE.app/Contents/Resources/app"))
MAIN_JS = APP_ROOT / "out" / "jetskiAgent" / "main.js"
WORKBENCH_JS = APP_ROOT / "out" / "vs" / "workbench" / "workbench.desktop.main.js"
PROD_JSON = APP_ROOT / "product.json"


def update_checksum(prod_data: dict, relative_path: str, file_path: Path) -> str:
    with open(file_path, "rb") as f:
        digest = base64.b64encode(hashlib.sha256(f.read()).digest()).decode().rstrip("=")
    prod_data.setdefault("checksums", {})[relative_path] = digest
    return digest


def patch_jetski_agent(prod_data: dict) -> bool:
    if not MAIN_JS.is_file():
        print(f"Error: {MAIN_JS} does not exist.")
        return False

    with open(MAIN_JS, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()

    # 1. Patch Ois dragover and drop
    ois_orig = (
        'Ois=({onMediaDrop:e,allowVideo:t})=>{let[r,n]=ve(!1),a=ie(o=>{o.preventDefault();let l=Array.from(o.dataTransfer?.items||[]).'
        'some(d=>d.kind==="file"&&(d.type.startsWith("image/")||d.type==="application/pdf"||qM(d.type)||t&&R3n.includes(d.type)));'
        'n(l)},[t]),i=ie(o=>{o.preventDefault(),o.stopPropagation(),n(!1);let l=o.dataTransfer?.files;if(l){let d=new DataTransfer;'
        'Array.from(l).forEach(h=>{d.items.add(h)}),d.files.length>0&&e&&e(d.files)}},[e]),s=ie(o=>{o.preventDefault(),(o.relatedTarget===null||'
        'o.currentTarget instanceof HTMLElement&&o.relatedTarget instanceof Node&&!o.currentTarget.contains(o.relatedTarget))&&n(!1)},[]);'
        'return ze(()=>(window.addEventListener("dragover",a),window.addEventListener("drop",i),window.addEventListener("dragleave",s),()=>{'
        'window.removeEventListener("dragover",a),window.removeEventListener("drop",i),window.removeEventListener("dragleave",s)}),[a,i,s]),{isDraggingMedia:r}}'
    )

    ois_new = (
        'Ois=({onMediaDrop:e,allowVideo:t})=>{let[r,n]=ve(!1),a=ie(o=>{o.preventDefault();let l=Array.from(o.dataTransfer?.items||[]).'
        'some(d=>d.kind==="file");n(l)},[]),i=ie(async o=>{o.preventDefault(),o.stopPropagation(),n(!1);let it=o.dataTransfer?.items,d=new DataTransfer;'
        'if(it&&it.length>0){let c=async en=>{if(!en)return;if(en.isFile)await new Promise(res=>en.file(f=>{f&&d.items.add(f),res()},()=>res()));'
        'else if(en.isDirectory){let rdr=en.createReader(),ra=async()=>{let ents=await new Promise(res=>rdr.readEntries(res,()=>res([])));'
        'if(ents&&ents.length>0){for(let sub of ents)await c(sub);await ra()}};await ra()}};for(let item of Array.from(it)){'
        'let en=item.webkitGetAsEntry?item.webkitGetAsEntry():null;en?await c(en):item.kind==="file"&&item.getAsFile()&&d.items.add(item.getAsFile())}}'
        'if(d.files.length===0&&o.dataTransfer?.files)Array.from(o.dataTransfer.files).forEach(h=>d.items.add(h));d.files.length>0&&e&&e(d.files)},[e]),'
        's=ie(o=>{o.preventDefault(),(o.relatedTarget===null||o.currentTarget instanceof HTMLElement&&o.relatedTarget instanceof Node&&'
        '!o.currentTarget.contains(o.relatedTarget))&&n(!1)},[]);return ze(()=>(window.addEventListener("dragover",a),window.addEventListener("drop",i),'
        'window.addEventListener("dragleave",s),()=>{window.removeEventListener("dragover",a),window.removeEventListener("drop",i),'
        'window.removeEventListener("dragleave",s)}),[a,i,s]),{isDraggingMedia:r}}'
    )

    if ois_orig in code:
        code = code.replace(ois_orig, ois_new)
        print("  [PATCHED jetskiAgent] Ois dragover unblocking and recursive folder drop traversal.")
    elif 'd=>d.kind==="file");n(l)' in code:
        print("  [ALREADY PATCHED jetskiAgent] Ois already updated.")

    # 2. Patch batch ceiling and limits
    if ",lxn=5," in code:
        code = code.replace(",lxn=5,", ",lxn=50,")
        print("  [PATCHED jetskiAgent] Batch upload ceiling lxn expanded from 5 to 50 items.")
    if "y3n=20*1024*1024" in code:
        code = code.replace("y3n=20*1024*1024", "y3n=100*1024*1024")
        print("  [PATCHED jetskiAgent] Text attachment ceiling y3n expanded from 20MB to 100MB.")
    if "A3n=10*1024*1024" in code:
        code = code.replace("A3n=10*1024*1024", "A3n=100*1024*1024")
        print("  [PATCHED jetskiAgent] PDF attachment ceiling A3n expanded from 10MB to 100MB.")

    # 3. Patch Mis hook for dual file & folder selection
    mis_orig = (
        'var Mis=({onFilesSelected:e})=>{let t=Me(null),r=ie(n=>{let a=n.target;a.files&&e(a.files)},[e]);'
        'return{openFileDialog:ie(()=>{t.current?.click()},[]),fileInputRef:t,handleFileChange:r}}'
    )
    mis_new = (
        'var Mis=({onFilesSelected:e})=>{let t=Me(null),k=Me(null),r=ie(n=>{let a=n.target;a.files&&e(a.files)},[e]);'
        'return{openFileDialog:ie(()=>{t.current?.click()},[]),openFolderDialog:ie(()=>{k.current?.click()},[]),'
        'fileInputRef:t,folderInputRef:k,handleFileChange:r}}'
    )
    if mis_orig in code:
        code = code.replace(mis_orig, mis_new)
        print("  [PATCHED jetskiAgent] Mis hook with openFolderDialog and folderInputRef.")

    # 4. Patch jSs caller
    jss_call_orig = '{openFileDialog:de,fileInputRef:Ae,handleFileChange:Re}=Mis({onFilesSelected:d})'
    jss_call_new = '{openFileDialog:de,openFolderDialog:openFolderDialog,fileInputRef:Ae,folderInputRef:folderInputRef,handleFileChange:Re}=Mis({onFilesSelected:d})'
    if jss_call_orig in code:
        code = code.replace(jss_call_orig, jss_call_new)
        print("  [PATCHED jetskiAgent] jSs destructuring openFolderDialog and folderInputRef.")

    # 5. Patch kt toolbar buttons in jSs
    kt_orig = (
        'le!=="Cider"&&je.push({icon:Qe=>f(xe,{name:"image",size:Qe.width?Number(Qe.width):14,className:Qe.className}),label:"Media",onClick:de}),'
        'je.push({icon:Qe=>f(xe,{name:"alternate_email",size:Qe.width?Number(Qe.width):14,className:Qe.className}),label:"Mentions",onClick:gt})'
    )
    kt_new = (
        'le!=="Cider"&&je.push({icon:Qe=>f(xe,{name:"image",size:Qe.width?Number(Qe.width):14,className:Qe.className}),label:"Media",onClick:de}),'
        'le!=="Cider"&&je.push({icon:Qe=>f(xe,{name:"folder_open",size:Qe.width?Number(Qe.width):14,className:Qe.className}),label:"Folder",onClick:openFolderDialog}),'
        'je.push({icon:Qe=>f(xe,{name:"alternate_email",size:Qe.width?Number(Qe.width):14,className:Qe.className}),label:"Mentions",onClick:gt})'
    )
    if kt_orig in code:
        code = code.replace(kt_orig, kt_new)
        print("  [PATCHED jetskiAgent] Action toolbar with 'Folder' upload button.")

    kt_dep_orig = '[gt,Pe,Qt,de,E,le,ye]'
    kt_dep_new = '[gt,Pe,Qt,de,openFolderDialog,E,le,ye]'
    if kt_dep_orig in code:
        code = code.replace(kt_dep_orig, kt_dep_new)
        print("  [PATCHED jetskiAgent] kt dependencies including openFolderDialog.")

    # 6. Render folder file input in jSs
    jsx_orig = 'f(sxn,{ref:Ae,onChange:Re,accept:void 0,multiple:!0}),f("div",{className:"flex items-center gap-1 min-w-0",children:'
    jsx_new = (
        'f(sxn,{ref:Ae,onChange:Re,accept:void 0,multiple:!0}),'
        'f("input",{type:"file",ref:folderInputRef,onChange:Re,webkitdirectory:"",directory:"",multiple:!0,className:"hidden"}),'
        'f("div",{className:"flex items-center gap-1 min-w-0",children:'
    )
    if jsx_orig in code:
        code = code.replace(jsx_orig, jsx_new)
        print("  [PATCHED jetskiAgent] Rendered folder input element with webkitdirectory.")

    # Write patched file atomically
    tmp_main = MAIN_JS.with_name("main.tmp.js")
    with open(tmp_main, "w", encoding="utf-8") as f:
        f.write(code)

    syntax_res = subprocess.run(["node", "--check", str(tmp_main)], capture_output=True, text=True)
    if syntax_res.returncode != 0:
        print(f"Error: Syntax check failed on patched jetskiAgent/main.js:\n{syntax_res.stderr}")
        tmp_main.unlink(missing_ok=True)
        return False

    os.replace(tmp_main, MAIN_JS)
    h = update_checksum(prod_data, "jetskiAgent/main.js", MAIN_JS)
    print(f"  [SUCCESS jetskiAgent] main.js updated with checksum {h}")
    return True


def patch_workbench(prod_data: dict) -> bool:
    if not WORKBENCH_JS.is_file():
        print(f"Error: {WORKBENCH_JS} does not exist.")
        return False

    with open(WORKBENCH_JS, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()

    # 1. Patch restrictive accept attribute in Eqo caller
    accept_orig = (
        'E(Eqo,{ref:Me,onChange:Ve,accept:".png,.jpg,.jpeg,.gif,image/png,image/jpeg,image/gif,video/webm,.mp4,video/mp4,'
        '.pdf,application/pdf,.txt,text/plain,.csv,text/csv,.json,application/json,.md,text/markdown,.py,text/x-python,'
        '.js,.mjs,text/javascript,.ts,.tsx,text/x-typescript,.html,.htm,text/html,.css,text/css",multiple:!0})'
    )
    accept_new = 'E(Eqo,{ref:Me,onChange:Ve,accept:void 0,multiple:!0})'
    if accept_orig in code:
        code = code.replace(accept_orig, accept_new)
        print("  [PATCHED workbench] Restrictive accept attribute removed from FileUploadInput (accept: void 0).")
    elif 'E(Eqo,{ref:Me,onChange:Ve,accept:void 0,multiple:!0})' in code:
        print("  [ALREADY PATCHED workbench] Accept attribute already removed.")
    else:
        print("  [WARN workbench] Accept attribute pattern not found.")

    # 2. Patch abu dragover and drop listeners
    abu_orig = (
        'abu=({onMediaDrop:t,allowVideo:e})=>{let[i,n]=We(!1),r=me(a=>{a.preventDefault();let l=Array.from(a.dataTransfer?.items||[]).'
        'some(d=>d.kind==="file"&&(d.type.startsWith("image/")||d.type==="application/pdf"||rZe(d.type)||e&&Rzo.includes(d.type)));'
        'n(l)},[e]),s=me(a=>{a.preventDefault(),a.stopPropagation(),n(!1);let l=a.dataTransfer?.files;if(l){let d=new DataTransfer;'
        'Array.from(l).forEach(u=>{(u.type.startsWith("image/")||u.type.startsWith("video/")||u.type==="application/pdf"||rZe(u.type,u.name))&&'
        'd.items.add(u)}),d.files.length>0&&t&&t(d.files)}},[t]),o=me(a=>{a.preventDefault(),(a.relatedTarget===null||'
        'a.currentTarget instanceof HTMLElement&&a.relatedTarget instanceof Node&&!a.currentTarget.contains(a.relatedTarget))&&n(!1)},[]);'
        'return yt(()=>(window.addEventListener("dragover",r),window.addEventListener("drop",s),window.addEventListener("dragleave",o),()=>{'
        'window.removeEventListener("dragover",r),window.removeEventListener("drop",s),window.removeEventListener("dragleave",o)}),[r,s,o]),{isDraggingMedia:i}}'
    )

    abu_new = (
        'abu=({onMediaDrop:t,allowVideo:e})=>{let[i,n]=We(!1),r=me(a=>{a.preventDefault();let l=Array.from(a.dataTransfer?.items||[]).'
        'some(d=>d.kind==="file");n(l)},[]),s=me(async a=>{a.preventDefault(),a.stopPropagation(),n(!1);let it=a.dataTransfer?.items,d=new DataTransfer;'
        'if(it&&it.length>0){let c=async en=>{if(!en)return;if(en.isFile)await new Promise(res=>en.file(f=>{f&&d.items.add(f),res()},()=>res()));'
        'else if(en.isDirectory){let rdr=en.createReader(),ra=async()=>{let ents=await new Promise(res=>rdr.readEntries(res,()=>res([])));'
        'if(ents&&ents.length>0){for(let sub of ents)await c(sub);await ra()}};await ra()}};for(let item of Array.from(it)){'
        'let en=item.webkitGetAsEntry?item.webkitGetAsEntry():null;en?await c(en):item.kind==="file"&&item.getAsFile()&&d.items.add(item.getAsFile())}}'
        'if(d.files.length===0&&a.dataTransfer?.files)Array.from(a.dataTransfer.files).forEach(u=>d.items.add(u));d.files.length>0&&t&&t(d.files)},[t]),'
        'o=me(a=>{a.preventDefault(),(a.relatedTarget===null||a.currentTarget instanceof HTMLElement&&a.relatedTarget instanceof Node&&'
        '!a.currentTarget.contains(a.relatedTarget))&&n(!1)},[]);return yt(()=>(window.addEventListener("dragover",r),window.addEventListener("drop",s),'
        'window.addEventListener("dragleave",o),()=>{window.removeEventListener("dragover",r),window.removeEventListener("drop",s),'
        'window.removeEventListener("dragleave",o)}),[r,s,o]),{isDraggingMedia:i}}'
    )

    if abu_orig in code:
        code = code.replace(abu_orig, abu_new)
        print("  [PATCHED workbench] abu dragover unblocking and recursive folder drop traversal.")
    elif 'd=>d.kind==="file");n(l)' in code:
        print("  [ALREADY PATCHED workbench] abu already updated.")
    else:
        print("  [WARN workbench] abu pattern not found.")

    # 3. Patch jgu text extension mapping
    jgu_orig = 'jgu={ts:"text/x-typescript",tsx:"text/x-typescript",py:"text/x-python"}'
    jgu_new = (
        'jgu={ts:"text/x-typescript",tsx:"text/x-typescript",py:"text/x-python",'
        'vtt:"text/vtt",srt:"text/plain",yaml:"text/yaml",yml:"text/yaml",xml:"text/xml",'
        'sh:"text/x-sh",sql:"text/x-sql",log:"text/plain"}'
    )
    if jgu_orig in code:
        code = code.replace(jgu_orig, jgu_new)
        print("  [PATCHED workbench] Broadened text/code extensions in jgu (.vtt, .yaml, .sql, .sh, .log).")
    elif 'vtt:"text/vtt"' in code:
        print("  [ALREADY PATCHED workbench] jgu already contains .vtt mapping.")

    # 4. Patch gbu generic binary / arrayBuffer fallback
    gbu_orig = (
        'if(!a.includes(o.type.toLowerCase()))return;let u=1*1024*1024,h=1024;try{let{inlineData:f,mimeType:g}=await yqo(o,u,h),'
        '{inlineData:m}=await yqo(o,EDn,yDn),b=Ut(w$,{mimeType:g,description:o.name,payload:{case:"inlineData",value:f},thumbnail:m});'
        't([b])}catch{}}'
    )
    gbu_new = (
        'if(a.includes(o.type.toLowerCase())){let u=1*1024*1024,h=1024;try{let{inlineData:f,mimeType:g}=await yqo(o,u,h),'
        '{inlineData:m}=await yqo(o,EDn,yDn),b=Ut(w$,{mimeType:g,description:o.name,payload:{case:"inlineData",value:f},thumbnail:m});'
        't([b])}catch{}return}try{let f=await o.arrayBuffer(),g=new Uint8Array(f),mt=o.type||"application/octet-stream",'
        'b=Ut(w$,{mimeType:mt,description:o.name,payload:{case:"inlineData",value:g}});t([b])}catch(f){console.error("Failed to process attachment",f)}}'
    )
    if gbu_orig in code:
        code = code.replace(gbu_orig, gbu_new)
        print("  [PATCHED workbench] Generic binary arrayBuffer fallback added to gbu (.pptx, .docx, .xlsx).")
    elif 'mt=o.type||"application/octet-stream"' in code:
        print("  [ALREADY PATCHED workbench] gbu generic binary fallback already present.")
    else:
        print("  [WARN workbench] gbu pattern not found.")

    # 5. Patch size ceilings in workbench
    if "wzo=10*1024*1024" in code:
        code = code.replace("wzo=10*1024*1024", "wzo=100*1024*1024")
        print("  [PATCHED workbench] PDF ceiling wzo expanded from 10MB to 100MB.")
    if "Azo=1*1024*1024" in code:
        code = code.replace("Azo=1*1024*1024", "Azo=100*1024*1024")
        print("  [PATCHED workbench] Text ceiling Azo expanded from 1MB to 100MB.")

    # Write patched file atomically
    tmp_wb = WORKBENCH_JS.with_name("workbench.tmp.js")
    with open(tmp_wb, "w", encoding="utf-8") as f:
        f.write(code)

    syntax_res = subprocess.run(["node", "--check", str(tmp_wb)], capture_output=True, text=True)
    if syntax_res.returncode != 0:
        print(f"Error: Syntax check failed on patched workbench.desktop.main.js:\n{syntax_res.stderr}")
        tmp_wb.unlink(missing_ok=True)
        return False

    os.replace(tmp_wb, WORKBENCH_JS)
    h = update_checksum(prod_data, "vs/workbench/workbench.desktop.main.js", WORKBENCH_JS)
    print(f"  [SUCCESS workbench] workbench.desktop.main.js updated with checksum {h}")
    return True


def apply_patch() -> bool:
    print(f"Target App Root: {APP_ROOT}")
    if not PROD_JSON.is_file():
        print(f"Error: {PROD_JSON} does not exist.")
        return False

    with open(PROD_JSON, "r", encoding="utf-8") as f:
        prod_data = json.load(f)

    success = True
    print("\n--- Patching jetskiAgent/main.js ---")
    success &= patch_jetski_agent(prod_data)

    print("\n--- Patching vs/workbench/workbench.desktop.main.js ---")
    success &= patch_workbench(prod_data)

    if not success:
        print("\n[ERROR] One or more patch stages failed.")
        return False

    # Atomically save updated product.json
    tmp_prod = PROD_JSON.with_suffix(".tmp")
    with open(tmp_prod, "w", encoding="utf-8") as f:
        json.dump(prod_data, f, indent=2)
    os.replace(tmp_prod, PROD_JSON)
    print("\n[SUCCESS] product.json updated with validated manifest checksums.")
    return True


if __name__ == "__main__":
    print("=== Applying Comprehensive Antigravity IDE Dual-Bundle File Intake Patch ===")
    ok = apply_patch()
    sys.exit(0 if ok else 1)
