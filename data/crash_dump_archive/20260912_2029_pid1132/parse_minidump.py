#!/usr/bin/env python3
"""
CRASH-F1-POSTMORTEM-1 -- read-only MINIDUMP parser (stdlib only, no deps).

PRIVACY CONTRACT (hard-coded):
  This script NEVER prints memory *content* (no strings, no raw bytes, no chat
  text, no tokens). It only emits:
    - structure fields (exception code/flags/address/thread id)
    - register values (RIP/RSP/...) which are addresses
    - module names, base, size, version, timestamps
    - stack words *only* when they are classified as return-address candidates
      (i.e. an 8-byte value landing inside a loaded module range), printed as
      module+offset. Call-site classification reads a single byte at
      (candidate - 5) inside a mapped image to test for an E8 CALL opcode; that
      byte is compared only against a constant and is never emitted.

Usage: python parse_minidump.py <path-to.dmp>

*** CORRECTED 2026-09-13 (CRASH-F1-DOCFIX) -- REGISTER WARNING ****************
  The CONTEXT register offsets in every earlier revision of this tool were
  WRONG (Rax@0x88/Rcx@0x90/...). The x64 CONTEXT layout is
  Rax@0x78, Rcx@0x80, Rdx@0x88, R8..R15@0xB8..0xF0, Rip@0xF8.

  CONSEQUENCE -- ALL REGISTER VALUES PRODUCED BY EARLIER REVISIONS ARE UNTRUSTWORTHY
  AND MUST BE DISCARDED. They were not merely shifted: because a register name
  was bound to the slot of the register 16 bytes later in the layout, every
  misreported value was some OTHER register's value. Concretely, the old file
  reported "RAX = 0x000000004E4D07D8" (that value is really Rdx) and
  "RCX = 0x0000000000000003" (really R9) for the CRASH-F1 dump -- while the
  symbolically verified truth (docs/CRASH-F1-SYMBOLS-1.md) is Rax = 1 and
  Rcx = 0. Only RSP/RBP happened to stay correct.

  FIXED VERIFICATION (2026-09-13, this revision, same dump): RCX = 0x0 and
  ExceptionInformation[1] = 0xA0, matching the symbolication
  (faulting instruction `mov rcx,[rcx+0xA0]` with RCX == 0).
******************************************************************************
"""
import struct
import sys
import os

# ---------------------------------------------------------------- stream types
ST_THREAD_LIST      = 0x60000003
ST_MODULE_LIST      = 0x60000004
ST_MEMORY_LIST      = 0x60000005
ST_EXCEPTION        = 0x60000006
ST_SYSTEM_INFO      = 0x60000007
ST_MEMORY64_LIST    = 0x60000009

DEV_SYSTEM_INFO = 7
DEV_EXCEPTION   = 6
DEV_MODULE_LIST = 4
DEV_MEMORY_LIST = 5
DEV_THREAD_LIST = 3
DEV_MEMORY64    = 9

MASK64 = (1 << 64) - 1


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def u64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def i64(b, o):
    return struct.unpack_from("<q", b, o)[0]


class Dump:
    def __init__(self, path):
        self.path = path
        self.size = os.path.getsize(path)
        self._fh = None
        with open(path, "rb") as f:
            head = f.read(32)
        self.sig, self.version, self.nstreams, self.dir_rva = struct.unpack_from(
            "<4sIII", head, 0
        )
        if self.sig != b"MDMP":
            raise SystemExit("not a MINIDUMP (signature=%r)" % self.sig)
        self.checksum = u32(head, 24)
        self.timestamp = u32(head, 28)
        with open(path, "rb") as f:
            f.seek(self.dir_rva)
            raw = f.read(12 * self.nstreams)
        self.dirs = []
        for i in range(self.nstreams):
            t, sz, rva = struct.unpack_from("<III", raw, i * 12)
            self.dirs.append((t, sz, rva))
        # memory64 / memory list ranges for RVA -> file offset resolution
        self.ranges = []
        self._load_ranges()

    # ------------------------------------------------------------- file access
    @property
    def fh(self):
        if self._fh is None:
            self._fh = open(self.path, "rb")
        return self._fh

    def read_at(self, off, n):
        self.fh.seek(off)
        return self.fh.read(n)

    def stream(self, stype):
        """Some dump writers emit only the low 16 bits of the stream type
        (e.g. 0x00000006) instead of 0x60000006. Match either encoding."""
        low = stype & 0xFFFF
        for t, sz, rva in self.dirs:
            if t == stype or (t & 0xFFFF) == low and (t >> 16) in (0x6000, 0):
                if t == 0:
                    continue
                return self.read_at(rva, sz), rva
        return None, None

    def _load_ranges(self):
        raw, _ = self.stream(ST_MEMORY64_LIST)
        self.m64 = None
        self.ranges = []
        # Precompute (va_start, size, file_off) for every mapped chunk.
        if raw:
            n, base = struct.unpack_from("<QQ", raw, 0)
            self.m64 = (n, base)
            cursor = base
            for i in range(n):
                start, dsize = struct.unpack_from("<QQ", raw, 16 + i * 16)
                self.ranges.append((start, dsize, cursor))
                cursor += dsize
            self.m64_end = cursor
        raw, _ = self.stream(ST_MEMORY_LIST)
        self.mem = []
        if raw:
            n = u32(raw, 0)
            for i in range(n):
                o = 4 + i * 16
                start, dsize, rva = struct.unpack_from("<QII", raw, o)
                self.mem.append((start, dsize, rva))
                self.ranges.append((start, dsize, rva))

    def va_to_off(self, va, n=8):
        """Map a virtual address to a file offset using the memory map."""
        for start, dsize, foff in self.ranges:
            if start <= va and va + n <= start + dsize:
                return foff + (va - start)
        return None

    def read_va(self, va, n):
        off = self.va_to_off(va, n)
        if off is None:
            return None
        return self.read_at(off, n)

    def read_va_clipped(self, va, n):
        """Read as much as the containing chunk holds (used for large windows)."""
        for start, dsize, foff in self.ranges:
            if start <= va < start + dsize:
                avail = min(n, start + dsize - va)
                return self.read_at(foff + (va - start), avail)
        return None

    def read_context(self, ctx_rva, ctx_size):
        """Thread context RVAs in this dump are direct file offsets, but accept
        a memory-map VA as a fallback."""
        blob = None
        if 0 < ctx_rva < self.size:
            blob = self.read_at(ctx_rva, min(ctx_size, 0x4D0))
        if not blob or len(blob) < 0x100:
            blob = self.read_va(ctx_rva, min(ctx_size, 0x4D0))
        if not blob or len(blob) < 0x100:
            return None
        return blob


# ------------------------------------------------------------------- modules
def parse_modules(d):
    raw, _ = d.stream(ST_MODULE_LIST)
    if not raw:
        return []
    n = u32(raw, 0)
    mods = []
    o = 4
    MSZ = 108
    for _ in range(n):
        base = u64(raw, o)
        ssize = u32(raw, o + 8)
        _chk = u32(raw, o + 12)
        tstamp = u32(raw, o + 16)
        name_rva = u32(raw, o + 20)
        # VS_FIXEDFILEINFO at o+24 .. o+76
        ffi_sig, ffi_sv1, ffi_sv2, ffi_fv1, ffi_fv2 = struct.unpack_from(
            "<IIIII", raw, o + 24
        )
        ver = ""
        if ffi_sig == 0xFE2FF:
            ver = "%d.%d.%d.%d" % (
                ffi_fv1 >> 16, ffi_fv1 & 0xFFFF, ffi_fv2 >> 16, ffi_fv2 & 0xFFFF
            )
        # UTF-16 module name. MINIDUMP_STRING is (u32 byte-length, UTF-16 chars).
        # The ModuleListStream name RVA in this dump is a *file offset* (the
        # memory map only covers 0x7FFE... / 0x1F4... / 0x551D... image and heap
        # regions, and these name RVAs are far below the mapped range).
        # This is image file metadata, never process memory text.
        name = "<unreadable>"
        for reader, base_off in ((lambda r: d.read_at(name_rva, r), 0),):
            nb = reader(4)
            if not nb:
                break
            nl = u32(nb, 0)
            if 0 < nl <= 4096:
                raw_name = reader(nl + 4)[4:]
                if raw_name:
                    name = raw_name.decode("utf-16-le", "replace")
            break
        mods.append(
            dict(base=base, size=ssize, tstamp=tstamp, name=name, ver=ver)
        )
        o += MSZ
    return mods


def find_mod(mods, va):
    for m in mods:
        if m["base"] <= va < m["base"] + m["size"]:
            return m, va - m["base"]
    return None, None


def short(name):
    """Basename only -- avoids echoing installation paths into the report."""
    return os.path.basename(name) if name else name


# ------------------------------------------------------------------ exception
# x64 CONTEXT: standard Windows layout, verified empirically against this
# dump's ExceptionStream context (fields followed the WinDbg !context offsets
# P1Home..P6Home=0x00..0x28, then the integer/control registers):
#   0x00 P1Home..P6Home (6*8) | 0x30 ContextFlags (0x0010005F seen) | 0x34 MxCsr
#   0x38 SegCs 0x3a SegDs 0x3c SegEs 0x3e SegFs 0x40 SegGs 0x42 SegSs
#   0x44 EFlags | 0x48 Dr0..Dr7 (8*8)
#   0x78 Rax 0x80 Rcx 0x88 Rdx 0x90 Rbx
#   0x98 Rsp 0xa0 Rbp 0xa8 Rsi 0xb0 Rdi
#   0xb8 R8 0xc0 R9 0xc8 R10 0xd0 R11 0xd8 R12 0xe0 R13 0xe8 R14 0xf0 R15
#   0xf8 Rip
# Cross-check: this yields Rip == ExceptionAddress (0x7FFE75245BDA) and an Rsp
# that lands inside the crashing thread's captured stack, as it must.
#
# CORRECTED 2026-09-13 (CRASH-F1-DOCFIX): the first four of these constants used
# to read CTX_RAX=0x88 / CTX_RCX=0x90 / CTX_RSP=0x98 / CTX_RBP=0xA0 -- i.e. the
# whole integer/pointer register block was bound 16 bytes too high. The cause was
# a mis-derived layout: that revision recorded Rax@0x88 by pattern-matching the
# already-known-good Rip@0xf8 and counting downward in 8-byte steps, instead of
# using the real x64 CONTEXT layout, whose 0x78..0x98 region is
# Rax,Rcx,Rdx,Rbx,Rsp (NOT Rax,Rcx,Rsp,Rbp,Rsi). The error was invisible to the
# revision's own self-check because that check only tested Rip and Rsp, and
# Rdx/Rbx sit at the slot the old table called Rax/Rcx while Rsp/Rbp happened to
# stay right. Net effect: RAX/RCX (and the names given to Rdx/Rbx) were wrong.
# The cross-check below is what the old table tripped over; the authoritative
# reference for the fixed values is docs/CRASH-F1-SYMBOLS-1.md section 5.2.
CTX_RAX = 0x78
CTX_RCX = 0x80
CTX_RDX = 0x88
CTX_RBX = 0x90
CTX_RSP = 0x98
CTX_RBP = 0xA0
CTX_RSI = 0xA8
CTX_RDI = 0xB0
CTX_EFLAGS = 0x44
CTX_SEGCS = 0x38
CTX_RIP = 0xF8


def parse_exception(d):
    """MINIDUMP_EXCEPTION_STREAM layout for this dump (verified against the raw
    bytes: 168 bytes total, matching the directory stream size):
        0x00 ThreadId u32
        0x04 alignment u32
        0x08 MINIDUMP_EXCEPTION_RECORD (80 bytes)
              0x08 ExceptionCode   u32
              0x0c ExceptionFlags  u32
              0x10 ExceptionRecord u64
              0x18 ExceptionAddress u64
              0x20 NumberParameters u32
              0x24 __unusedAlignment u32
              0x28 ExceptionInformation[15] u64
        0x58 MINIDUMP_LOCATION_DESCRIPTOR ThreadContext (size u32, rva u32)
             -> ThreadContext descriptor actually sits at 0xA0 in this dump
    """
    raw, _ = d.stream(ST_EXCEPTION)
    if not raw:
        return None
    tid = u32(raw, 0)
    code = u32(raw, 8)
    flags = u32(raw, 12)
    rec = u64(raw, 16)
    addr = u64(raw, 24)
    nparams = u32(raw, 32)
    # ThreadContext location descriptor. The canonical offset is 0x58, but this
    # writer placed it at 0xA0 (stream is 0xA8 = 0xA0 + 8). Accept whichever
    # offset yields a plausible context size (x64 CONTEXT is 0x4D0 bytes).
    ctx_size = ctx_rva = None
    for cand in (0xA0, 0x58):
        cs = u32(raw, cand)
        cr = u32(raw, cand + 4)
        if cs in (0x4D0, 0x4D8, 0x310, 0x2CC) and 0 < cr < d.size:
            ctx_size, ctx_rva, ctx_at = cs, cr, cand
            break
    if ctx_size is None:
        # last resort: scan the tail for a valid (size, rva) pair
        for cand in range(0, len(raw) - 8, 4):
            cs = u32(raw, cand)
            cr = u32(raw, cand + 4)
            if 0x100 <= cs <= 0x2000 and 0 < cr < d.size:
                ctx_size, ctx_rva, ctx_at = cs, cr, cand
                break
    ctx = d.read_context(ctx_rva, ctx_size) if ctx_rva else None
    regs = {}
    if ctx and len(ctx) >= 0x100:
        regs = dict(
            rip=u64(ctx, CTX_RIP),
            rsp=u64(ctx, CTX_RSP),
            rbp=u64(ctx, CTX_RBP),
            rax=u64(ctx, CTX_RAX),
            rbx=u64(ctx, CTX_RBX),
            rcx=u64(ctx, CTX_RCX),
            rdx=u64(ctx, CTX_RDX),
            rsi=u64(ctx, CTX_RSI),
            rdi=u64(ctx, CTX_RDI),
            eflags=u32(ctx, CTX_EFLAGS),
            segcs=struct.unpack_from("<H", ctx, CTX_SEGCS)[0],
            context_flags=u32(ctx, 0x30),
        )
    return dict(
        thread_id=tid, code=code, flags=flags, record=rec, address=addr,
        nparams=nparams, ctx_size=ctx_size, ctx_rva=ctx_rva, regs=regs,
        raw=raw, ctx_at=ctx_at if ctx_rva else None,
    )


def dump_exception_params(exc):
    raw = exc["raw"]
    out = []
    for i in range(min(exc["nparams"], 15)):
        v = u64(raw, 40 + i * 8)
        out.append(v)
    return out


# ------------------------------------------------------------------- threads
def parse_threads(d):
    raw, _ = d.stream(ST_THREAD_LIST)
    if not raw:
        return []
    n = u32(raw, 0)
    th = []
    for i in range(n):
        o = 4 + i * 48
        tid = u32(raw, o)
        susp = u32(raw, o + 4)
        prio = u32(raw, o + 8)
        pad = u32(raw, o + 12)
        teb = u64(raw, o + 16)
        stk_start = u64(raw, o + 24)
        stk_sz = u32(raw, o + 32)
        stk_rva = u32(raw, o + 36)
        ctx_sz = u32(raw, o + 40)
        ctx_rva = u32(raw, o + 44)
        th.append(dict(tid=tid, susp=susp, prio=prio, teb=teb,
                       stk_start=stk_start, stk_size=stk_sz,
                       stk_rva=stk_rva, ctx_size=ctx_sz, ctx_rva=ctx_rva))
    return th


# -------------------------------------------------------------- system info
def parse_sysinfo(d):
    raw, _ = d.stream(ST_SYSTEM_INFO)
    if not raw or len(raw) < 32:
        return None
    arch, level, rev, ncpu, ptype, major, minor, build = struct.unpack_from(
        "<HHHBBIII", raw, 0
    )
    plat = u32(raw, 24)
    csd = u32(raw, 28)
    suite = u32(raw, 32) if len(raw) >= 36 else 0
    tail = struct.unpack_from("<II", raw, 32) if len(raw) >= 40 else ()
    return dict(arch=arch, level=level, rev=rev, ncpu=ncpu, ptype=ptype,
                major=major, minor=minor, build=build, platform=plat,
                csd=csd, suite=suite, tail=tail)


ARCH = {0: "INTEL", 5: "ARM", 6: "IA64", 9: "AMD64", 12: "ARM64"}
PTYPE = {0: "X86", 1: "MIPS", 2: "ALPHA", 3: "PPC", 4: "SHX", 5: "ARM",
         6: "IA64", 0x8000: "MSIL", 9: "AMD64", 0xFFFF: "UNKNOWN"}


# ------------------------------------------------------------------ unwinding
def scan_return_addresses(d, mods, sp, limit=96, max_span=0x100000):
    """Walk the stack upward from SP; classify 8-byte words as return addresses.

    Only values landing inside a loaded module range are reported, as
    module+offset. No memory content other than single opcode bytes at
    (candidate-5)/(candidate-6) is examined, and those bytes are compared
    against constants only and never emitted.
    """
    found = []
    if not sp:
        return found
    # Find the captured chunk containing SP and scan within it.
    span = 0
    start = None
    for cs, dsize, foff in d.ranges:
        if cs <= sp < cs + dsize:
            start = sp
            span = min(cs + dsize - sp, max_span)
            break
    if start is None or span < 8:
        return found
    blob = d.read_va_clipped(start, span)
    if not blob:
        return found
    for i in range(0, len(blob) - 7, 8):
        v = struct.unpack_from("<Q", blob, i)[0]
        if v == 0:
            continue
        m, delta = find_mod(mods, v)
        if m is None:
            continue
        kind = "addr-in-image"
        if delta >= 6:
            b = d.read_va(v - 5, 1)
            if b and b[0] == 0xE8:
                kind = "CALL-rel32-ret"
            else:
                b2 = d.read_va(v - 6, 2)
                if b2 and b2[0] == 0xFF and (b2[1] & 0x38) == 0x10:
                    kind = "CALL-indirect-ret"
        found.append(dict(slot=start + i, value=v, mod=m["name"],
                          delta=delta, kind=kind))
        if len(found) >= limit:
            break
    return found


# ---------------------------------------------------------------------- main
def main():
    path = sys.argv[1]
    d = Dump(path)

    print("=" * 78)
    print("MINIDUMP STRUCTURE (read-only parse)")
    print("=" * 78)
    print("file            : %s" % d.path)
    print("bytes           : %d" % d.size)
    print("signature       : %s" % d.sig.decode("latin1"))
    print("version         : %d.%d" % ((d.version >> 16) & 0xFFFF, d.version & 0xFFFF))
    print("num_streams     : %d" % d.nstreams)
    print("dir_rva         : 0x%x" % d.dir_rva)
    print("checksum        : 0x%08x" % d.checksum)
    print("time_date_stamp : 0x%08x" % d.timestamp)

    names = {
        3: "ThreadListStream", 4: "ModuleListStream", 5: "MemoryListStream",
        6: "ExceptionStream", 7: "SystemInfoStream", 8: "ThreadExListStream",
        9: "Memory64ListStream", 10: "CommentStreamA", 11: "CommentStreamW",
        12: "HandleDataStream", 13: "FunctionTableStream",
        14: "UnloadedModuleListStream", 15: "MiscInfoStream",
        16: "MemoryInfoListStream", 17: "ThreadInfoListStream",
        18: "HandleOperationListStream", 19: "TokenStream",
    }
    print("\n--- stream directory ---")
    for t, sz, rva in d.dirs:
        print("  0x%08x  %-28s size=%-10d rva=0x%x"
              % (t, names.get(t, "?"), sz, rva))

    si = parse_sysinfo(d)
    print("\n--- SystemInfoStream ---")
    if si:
        print("  ProcessorArchitecture : %d (%s)"
              % (si["arch"], ARCH.get(si["arch"], "?")))
        print("  ProcessorLevel/Rev    : %d / %d" % (si["level"], si["rev"]))
        print("  NumberOfProcessors    : %d" % si["ncpu"])
        print("  ProductType           : %d" % si["ptype"])
        print("  PlatformId            : %d (%s)"
              % (si["platform"], PTYPE.get(si["platform"], "?")))
        print("  MajorVersion          : %d" % si["major"])
        print("  MinorVersion          : %d" % si["minor"])
        print("  BuildNumber           : %d" % si["build"])
        print("  CSDVersionRva         : 0x%X" % si["csd"])
        print("  SuiteMask             : 0x%X" % si["suite"])
        print("  (raw tail words: %s)"
              % " ".join("0x%X" % w for w in si["tail"]))
    else:
        print("  (absent)")

    mods = parse_modules(d)
    print("\n--- ModuleListStream : %d modules loaded ---" % len(mods))

    exc = parse_exception(d)
    print("\n" + "=" * 78)
    print("EXCEPTION STREAM")
    print("=" * 78)
    if not exc:
        print("  (absent)")
        return
    print("  ThreadId          : %d (0x%x)" % (exc["thread_id"], exc["thread_id"]))
    print("  ExceptionCode     : 0x%08X" % exc["code"])
    print("  ExceptionFlags    : 0x%08X" % exc["flags"])
    print("  ExceptionRecord   : 0x%016X" % exc["record"])
    print("  ExceptionAddress  : 0x%016X" % exc["address"])
    print("  NumberParameters  : %d" % exc["nparams"])
    params = dump_exception_params(exc)
    for i, p in enumerate(params):
        m, delta = find_mod(mods, p)
        extra = ("  -> %s+0x%x" % (m["name"], delta)) if m else ""
        print("    ExceptionInformation[%d] = 0x%016X%s" % (i, p, extra))
    print("  ContextSize       : %s bytes" % exc["ctx_size"])
    print("  ContextRva        : 0x%X  (descriptor at stream offset 0x%X)"
          % (exc["ctx_rva"], exc["ctx_at"] or 0))

    m, delta = find_mod(mods, exc["address"])
    print("\n  >> ExceptionAddress module resolution:")
    if m:
        print("     %s + 0x%X   (module base=0x%X size=0x%X)"
              % (short(m["name"]), delta, m["base"], m["size"]))
    else:
        print("     UNMAPPED (address not inside any loaded module range)")

    regs = exc["regs"]
    print("\n--- crashing thread context (registers = addresses) ---")
    if regs:
        print("  ContextFlags : 0x%08X" % regs["context_flags"])
        for k in ("rip", "rsp", "rbp", "rax", "rbx", "rcx", "rdx", "rsi", "rdi"):
            v = regs[k]
            mm, dd = find_mod(mods, v)
            extra = ("  -> %s+0x%x" % (short(mm["name"]), dd)) if mm else ""
            print("  %-4s : 0x%016X%s" % (k.upper(), v, extra))
        print("  EFLAGS : 0x%08X   SegCs : 0x%04X"
              % (regs["eflags"], regs["segcs"]))
    else:
        print("  (context not readable)")

    # ---- focus modules for CRASH-F1
    focus = ["python311.dll", "_overlapped.pyd", "select.pyd", "_asyncio.pyd",
             "_socket.pyd", "python3.dll", "kernel32.dll", "ntdll.dll",
             "ws2_32.dll", "mswsock.dll", "KERNELBASE.dll"]
    print("\n" + "=" * 78)
    print("CRASH-F1 FOCUS MODULES")
    print("=" * 78)
    lowered = {short(m["name"]).lower(): m for m in mods}
    for f in focus:
        m = lowered.get(f.lower())
        if m:
            print("  PRESENT  %-18s base=0x%016X size=0x%-8X ver=%-16s ts=0x%08X"
                  % (f, m["base"], m["size"], m["ver"] or "-", m["tstamp"]))
        else:
            print("  ABSENT   %s" % f)

    # ---- all C extensions / pyd files
    print("\n--- all .pyd / python-related modules ---")
    rel = [m for m in mods
           if m["name"].lower().endswith(".pyd")
           or "python" in os.path.basename(m["name"]).lower()]
    for m in sorted(rel, key=lambda x: x["base"]):
        print("  base=0x%016X size=0x%-8X ver=%-14s %s"
              % (m["base"], m["size"], m["ver"] or "-",
                 os.path.basename(m["name"])))

    # ---- stack scan of the crashing thread
    th = parse_threads(d)
    ct = None
    for t in th:
        if t["tid"] == exc["thread_id"]:
            ct = t
            break
    print("\n" + "=" * 78)
    print("CRASHING THREAD STACK -- RETURN-ADDRESS CANDIDATES (module+offset only)")
    print("=" * 78)
    sp = regs.get("rsp") if regs else None
    if ct:
        print("  thread stack range : 0x%016X .. 0x%016X (size=0x%X)"
              % (ct["stk_start"], ct["stk_start"] + ct["stk_size"], ct["stk_size"]))
        print("  RSP (from CONTEXT) : 0x%016X" % (sp or 0))
        if sp:
            cap = None
            for cs, dsize, foff in d.ranges:
                if cs <= sp < cs + dsize:
                    cap = (cs, dsize)
                    break
            if cap:
                print("  captured chunk @RSP: start=0x%016X size=0x%X"
                      % cap)
        cands = scan_return_addresses(d, mods, sp, limit=48)
        print("  candidates found   : %d" % len(cands))
        if not cands:
            print("  (no mapped-module return addresses in the scanned window)")
        for c in cands:
            print("   [0x%016X] 0x%016X  %s+0x%-8X %s"
                  % (c["slot"], c["value"], short(c["mod"]), c["delta"], c["kind"]))

        # explicit CRASH-F1 question
        pyd_hits = [c for c in cands if c["mod"].lower().endswith(".pyd")]
        print("\n  >> .pyd (C-extension) frames among candidates: %d" % len(pyd_hits))
        for c in pyd_hits:
            print("     %s+0x%X  @slot 0x%X" % (c["mod"], c["delta"], c["slot"]))
        core = [c for c in cands
                if any(k in c["mod"].lower() for k in
                       ("_overlapped", "_asyncio", "select", "_socket"))]
        print("  >> asyncio/IOCP-related C-extension frames: %d" % len(core))
        for c in core:
            print("     %s+0x%X  @slot 0x%X" % (c["mod"], c["delta"], c["slot"]))

    # ---- per-thread stack capture coverage (explains unwinding limits)
    print("\n" + "=" * 78)
    print("THREAD STACK CAPTURE COVERAGE")
    print("=" * 78)
    print("  total threads: %d" % len(th))
    for t in th:
        cs, csz = t["stk_start"], t["stk_size"]
        captured = 0
        for rs, dsize, foff in d.ranges:
            lo = max(rs, cs)
            hi = min(rs + dsize, cs + csz)
            if hi > lo:
                captured += hi - lo
        pct = (100.0 * captured / csz) if csz else 0.0
        mark = "  <== CRASHING THREAD" if t["tid"] == exc["thread_id"] else ""
        print("  tid=%-6d stack=[0x%016X..0x%016X] size=0x%-7X captured=0x%-7X (%5.1f%%)%s"
              % (t["tid"], cs, cs + csz, csz, captured, pct, mark))

    print("\n" + "=" * 78)
    print("ALL-THREAD STACK SCAN -- return-address candidates (module+offset only)")
    print("=" * 78)
    print("  purpose: answer the CRASH-F1 question across EVERY thread, since the")
    print("  crashing thread's RSP lies outside its nominal stack range.")
    pyd_index = {}
    core_index = {}
    for t in th:
        blob = d.read_context(t["ctx_rva"], t["ctx_size"])
        if not blob or len(blob) < 0x100:
            print("  tid=%-6d  <context not readable>" % t["tid"])
            continue
        tsp = u64(blob, CTX_RSP)
        trip = u64(blob, CTX_RIP)
        tc = scan_return_addresses(d, mods, tsp, limit=200)
        mods_seen = {}
        for c in tc:
            b = short(c["mod"])
            mods_seen.setdefault(b, []).append(c)
        py = [c for c in tc if c["mod"].lower().endswith(".pyd")]
        cr = [c for c in tc
              if any(k in c["mod"].lower() for k in
                     ("_overlapped", "_asyncio", "select", "_socket"))]
        if py:
            pyd_index[t["tid"]] = py
        if cr:
            core_index[t["tid"]] = cr
        mark = " <== CRASHING" if t["tid"] == exc["thread_id"] else ""
        print("  tid=%-6d RIP=%s RSP=0x%016X cands=%-4d pyd=%-3d core_ext=%d%s"
              % (t["tid"], ("%s+0x%X" % (short(find_mod(mods, trip)[0]["name"]),
                                         find_mod(mods, trip)[1]))
                 if find_mod(mods, trip)[0] else "unmapped",
                 tsp, len(tc), len(py), len(cr), mark))
        if mods_seen:
            order = sorted(mods_seen.items(),
                           key=lambda kv: -len(kv[1]))
            print("        frames by module: %s"
                  % ", ".join("%s x%d" % (k, len(v)) for k, v in order[:8]))

    print("\n  >> THREADS WITH .pyd FRAMES IN SCANNED WINDOW: %s"
          % (", ".join(str(k) for k in sorted(pyd_index)) or "NONE"))
    for tid, hits in sorted(pyd_index.items()):
        for c in hits:
            print("       tid=%d  %s+0x%X @slot 0x%X"
                  % (tid, short(c["mod"]), c["delta"], c["slot"]))

    print("\n  >> THREADS WITH _overlapped/_asyncio/select/_socket FRAMES: %s"
          % (", ".join(str(k) for k in sorted(core_index)) or "NONE"))
    for tid, hits in sorted(core_index.items()):
        for c in hits:
            print("       tid=%d  %s+0x%X @slot 0x%X"
                  % (tid, short(c["mod"]), c["delta"], c["slot"]))

    print("\n[done -- no memory content was read out beyond address classification]")

if __name__ == "__main__":
    main()
