import os, sys

IMEM_FILE = "instructionmemory.txt"
DMEM_FILE = "datamemory.txt"
TEXT_BASE = 0x00400000
DATA_BASE = 0x10010000

RNAME = [
    "$zero","$at","$v0","$v1","$a0","$a1","$a2","$a3",
    "$t0","$t1","$t2","$t3","$t4","$t5","$t6","$t7",
    "$s0","$s1","$s2","$s3","$s4","$s5","$s6","$s7",
    "$t8","$t9","$k0","$k1","$gp","$sp","$fp","$ra",
]

# ── helpers ───────────────────────────────────────────────────

def b2u(b):   return int(b, 2)
def u2b(v):   return format(int(v) & 0xFFFFFFFF, "032b")
def s16(b):   v = b2u(b); return v - 0x10000 if v >= 0x8000 else v
def s32(v):   v = int(v) & 0xFFFFFFFF; return v - 0x100000000 if v >= 0x80000000 else v

# ── file I/O ──────────────────────────────────────────────────

def read_lines(path):
    with open(path) as f:
        return [l.strip() for l in f if l.strip()]

def load_imem():  return read_lines(IMEM_FILE)
def load_dmem():  return [b2u(l) for l in read_lines(DMEM_FILE)]

def save_dmem(dmem):
    with open(DMEM_FILE, "w") as f:
        for w in dmem: f.write(u2b(w) + "\n")

# ── instruction decode ────────────────────────────────────────

def decode(bits):
    return {
        "op":     bits[0:6],
        "rs":     b2u(bits[6:11]),
        "rt":     b2u(bits[11:16]),
        "rd":     b2u(bits[16:21]),
        "shamt":  b2u(bits[21:26]),
        "funct":  bits[26:32],
        "imm":    s16(bits[16:32]),      # sign-extended
        "uimm":   b2u(bits[16:32]),      # zero-extended (for lui/ori)
        "addr26": b2u(bits[6:32]),       # jump target
    }

# ── processor ─────────────────────────────────────────────────

class MIPSProcessor:

    def __init__(self):
        for f in (IMEM_FILE, DMEM_FILE):
            if not os.path.exists(f): sys.exit(f"Missing file: {f}")

        self.imem  = load_imem()
        self.dmem  = load_dmem()
        self.regs  = [0] * 32
        self.PC    = 0      # byte offset from TEXT_BASE
        self.cycle = 0

        print(f"Loaded {len(self.imem)} instructions, {len(self.dmem)} data words")

    def rr(self, i):        return self.regs[i]
    def rw(self, i, v):
        if i != 0: self.regs[i] = int(v) & 0xFFFFFFFF

    def mem_read(self, addr):
        idx = (addr - DATA_BASE) // 4
        return self.dmem[idx] if 0 <= idx < len(self.dmem) else 0

    def mem_write(self, addr, val):
        idx = (addr - DATA_BASE) // 4
        while len(self.dmem) <= idx: self.dmem.append(0)
        self.dmem[idx] = int(val) & 0xFFFFFFFF
        save_dmem(self.dmem)                 # real-time file update

    def jump_target(self, addr26):
        mars_next = TEXT_BASE + self.PC + 4
        return ((mars_next & 0xF0000000) | (addr26 << 2)) - TEXT_BASE

    # ── Stage 1: IF ───────────────────────────────────────────

    def IF(self):
        idx = self.PC // 4
        if idx >= len(self.imem): return None, "past end of program"
        bits = self.imem[idx]
        return bits, f"[IF]  PC={self.PC}  {bits}"

    # ── Stage 2: ID ───────────────────────────────────────────

    def ID(self, bits):
        d = decode(bits)
        d["rs_val"] = self.rr(d["rs"])
        d["rt_val"] = self.rr(d["rt"])
        return d, f"[ID]  op={d['op']} fn={d['funct']}  {RNAME[d['rs']]}={s32(d['rs_val'])}  {RNAME[d['rt']]}={s32(d['rt_val'])}  rd={RNAME[d['rd']]}  imm={d['imm']}"

    # ── Stage 3: EX ───────────────────────────────────────────

    def EX(self, d):
        op, fn    = d["op"], d["funct"]
        rs, rt    = s32(d["rs_val"]), s32(d["rt_val"])
        rsu, rtu  = d["rs_val"], d["rt_val"]
        imm, uimm = d["imm"], d["uimm"]

        alu, taken, halt = 0, False, False
        npc = self.PC + 4

        if   op == "001111": alu = (uimm << 16) & 0xFFFFFFFF           # lui
        elif op == "001101": alu = (rsu | uimm) & 0xFFFFFFFF           # ori
        elif op in ("001001","001000"): alu = (rs + imm) & 0xFFFFFFFF  # addiu/addi
        elif op in ("100011","101011"): alu = (rs + imm) & 0xFFFFFFFF  # lw/sw addr
        elif op == "000100":                                            # beq
            if rsu == rtu: taken, npc = True, self.PC + 4 + imm * 4
        elif op == "000010": taken, npc = True, self.jump_target(d["addr26"])  # j
        elif op == "000000":
            if   fn == "100000": alu = (rs + rt) & 0xFFFFFFFF          # add
            elif fn == "100110": alu = (rsu ^ rtu) & 0xFFFFFFFF        # xor
            elif fn == "000000": alu = (rtu << d["shamt"]) & 0xFFFFFFFF # sll
            elif fn == "001100": halt = True                            # syscall

        return {**d, "alu": alu, "taken": taken, "npc": npc, "halt": halt}, \
               f"[EX]  alu={s32(alu)}  branch={taken}  next_PC={npc}" + ("  HALT" if halt else "")

    # ── Stage 4: MEM ──────────────────────────────────────────

    def MEM(self, d):
        op, addr, mval = d["op"], d["alu"], 0
        if op == "100011":                                              # lw
            mval = self.mem_read(addr)
            log  = f"[MEM] LW  addr=0x{addr:08X}  -> {s32(mval)}"
        elif op == "101011":                                            # sw (writes file)
            mval = d["rt_val"]
            self.mem_write(addr, mval)
            log  = f"[MEM] SW  addr=0x{addr:08X}  <- {s32(mval)}  (datamemory.txt updated)"
        else:
            log = "[MEM] --"
        return {**d, "mval": mval}, log

    # ── Stage 5: WB ───────────────────────────────────────────

    def WB(self, d):
        op = d["op"]
        if op in ("001111","001101","001001","001000"):  reg, val = d["rt"], d["alu"]
        elif op == "100011":                             reg, val = d["rt"], d["mval"]
        elif op == "000000" and d["funct"] != "001100": reg, val = d["rd"], d["alu"]
        else:                                            return "[WB]  --"
        self.rw(reg, val)
        return f"[WB]  {RNAME[reg]} <- {s32(val)}"

    # ── step & run ────────────────────────────────────────────

    def step(self):
        self.cycle += 1
        bits, log_IF = self.IF()
        if bits is None:
            print(f"\n{log_IF}"); return False

        d,   log_ID  = self.ID(bits)
        d,   log_EX  = self.EX(d)
        d,   log_MEM = self.MEM(d)
        log_WB       = self.WB(d)
        self.PC      = d["npc"]

        print(f"\n--- Cycle {self.cycle} ---")
        for log in (log_IF, log_ID, log_EX, log_MEM, log_WB):
            print(" ", log)

        if d["halt"]:
            print("\n  syscall -- done."); return False
        return True

    def run(self):
        print("\nStarting simulation...\n")
        while self.step(): pass
        print(f"\nFinished in {self.cycle} cycles.")

# ── summary helpers ───────────────────────────────────────────

def show_registers(regs):
    print("\n-- Registers (non-zero) --")
    for i, v in enumerate(regs):
        if s32(v) != 0:
            print(f"  {RNAME[i]:6s}  =  {s32(v)}")

def show_data_memory(dmem):
    labels = [f"A[{i}]" for i in range(10)] + [f"P[{i}]" for i in range(10)]
    print("\n-- Data Memory (A and P arrays) --")
    for i in range(20):
        print(f"  {labels[i]:5s}  =  {s32(dmem[i])}")

def show_verification(dmem):
    A = [s32(dmem[i]) for i in range(10)]
    P = [s32(dmem[10+i]) for i in range(10)]
    print("\n-- Verification --")
    acc = 0
    for i in range(10):
        acc ^= A[i]
        mark = "OK" if P[i] == acc else "FAIL"
        print(f"  P[{i}] = {P[i]:3d}   expected = {acc:3d}   {mark}")

# ── main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== MIPS Processor -- Prefix XOR ===")

    cpu = MIPSProcessor()
    cpu.run()

    final_dmem = load_dmem()   # re-read from disk to confirm file was written correctly
    show_registers(cpu.regs)
    show_data_memory(final_dmem)
    show_verification(final_dmem)
