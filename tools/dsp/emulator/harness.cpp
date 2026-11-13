// SPDX-License-Identifier: GPL-3.0-only
// Offline harness for DSP56300 engine files, built on the dsp56300 interpreter (GPL v3).
// CPU-only: no device and no caller DSP program. Synthetic stubs stand in for the host
// caller (P:0200/0224/0226) and the host mixer (P:0367, a bare RTS).
// Needs access-hook.patch and peripherals-open.patch applied to the interpreter checkout.
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>
#include "dsp56kEmu/dsp.h"
#include "dsp56kEmu/memory.h"
#include "dsp56kEmu/peripherals.h"
using namespace dsp56k;

static uint32_t num(const std::string& s) {
    size_t used = 0; auto v = std::stoul(s, &used, 16);
    if (used != s.size() || v > 0xffffff) throw std::runtime_error("bad hex: " + s);
    return static_cast<uint32_t>(v);
}
static std::vector<TWord> readWords(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    std::vector<unsigned char> b{std::istreambuf_iterator<char>(f), {}};
    if (b.empty() || b.size() % 3) throw std::runtime_error("bad u24be file: " + path);
    std::vector<TWord> w;
    for (size_t i = 0; i < b.size(); i += 3) w.push_back((b[i] << 16) | (b[i + 1] << 8) | b[i + 2]);
    return w;
}

static std::function<void(TWord)> g_onInstruction;
static void instructionHook(void*, TWord pc) { if (g_onInstruction) g_onInstruction(pc); }
static std::vector<unsigned char> executedPC(0x4000, 0);

struct Recorder {
    DSP* dsp = nullptr;
    bool on = false;
    // key: area(2) write(1) addr(24) pc(24)
    std::unordered_map<uint64_t, uint64_t> counts;
    static void hook(void* ctx, EMemArea area, TWord addr, bool write) {
        auto* self = static_cast<Recorder*>(ctx);
        if (!self->on) return;
        const uint64_t pc = self->dsp->getCurrentInstructionPC() & 0xffffff;
        const uint64_t key = (uint64_t(area) << 49) | (uint64_t(write) << 48) | (uint64_t(addr & 0xffffff) << 24) | pc;
        ++self->counts[key];
        if (write && area == MemArea_X && addr >= 0x4d00 && addr <= 0x4d4f && pc >= 0x2180 && pc < 0x4000)
            self->transport.insert(addr);
        if (write && area == MemArea_X && addr >= 0x4e90 && addr <= 0x4e9f && pc >= 0x2180 && pc < 0x4000)
            self->header.insert(addr);
        // Ordered write log (WLOG): every write by shim or engine code to the logged X range, in order.
        if (write && area == MemArea_X && pc >= 0x2000 && pc < 0x4000)
            for (const auto& r : self->logRanges)
                if (addr >= r.first && addr <= r.second) { self->wseq.emplace_back(addr & 0xffffff, static_cast<TWord>(pc)); break; }
    }
    std::set<TWord> transport;  // engine-PC writes to X:4D00..4D4F in the current block
    std::set<TWord> header;     // engine-PC writes to X:4E90..4E9F in the current block
    std::vector<std::pair<TWord, TWord>> logRanges;  // WLOG X ranges, none by default
    std::vector<std::pair<TWord, TWord>> wseq;  // (address, pc) of logged writes in the current block
};

// One-shot poke during the next BLOCK, when the PC first reaches pc (before that instruction runs):
// models a host write that lands while the engine is servicing the queue.
struct PokeTrigger { TWord pc; EMemArea area; TWord addr, value; bool fired; };

class HarnessPeripherals final : public PeripheralsNop {
public:
    Recorder* rec = nullptr; char space;
    TWord d0 = 0;
    std::map<std::pair<TWord, TWord>, uint64_t> reads, writes;  // (address, pc)
    explicit HarnessPeripherals(char s) : space(s) {}
    TWord read(TWord address, Instruction) override {
        if (rec && rec->on) ++reads[{address, getDSP().getCurrentInstructionPC()}];
        return (space == 'Y' && address == 0xffffd0) ? d0 : 0;
    }
    void write(TWord address, TWord) override {
        if (rec && rec->on) ++writes[{address, getDSP().getCurrentInstructionPC()}];
    }
    const TWord* readAsPtr(TWord, Instruction) override { return nullptr; }
};

static const EReg CALLER_REGS[] = {Reg_A2, Reg_A1, Reg_A0, Reg_B2, Reg_B1, Reg_B0, Reg_X0, Reg_X1, Reg_Y0, Reg_Y1,
    Reg_R0, Reg_R1, Reg_R2, Reg_R3, Reg_R4, Reg_R5, Reg_R6, Reg_R7, Reg_N0, Reg_N1, Reg_N2, Reg_N3, Reg_N4, Reg_N5,
    Reg_N6, Reg_N7, Reg_M0, Reg_M1, Reg_M2, Reg_M3, Reg_M4, Reg_M5, Reg_M6, Reg_M7, Reg_LA, Reg_LC};

static TWord getReg(DSP& dsp, EReg r) {
    if (r == Reg_A2 || r == Reg_B2) { TReg8 v; dsp.readReg(r, v); return v.toWord(); }
    TReg24 v; dsp.readReg(r, v); return v.toWord();
}
static void setReg(DSP& dsp, EReg r, TWord value) {
    if (r == Reg_A2 || r == Reg_B2) { dsp.writeReg(r, TReg8(static_cast<uint8_t>(value & 0xff))); return; }
    dsp.writeReg(r, TReg24(value & 0xffffff));
}

int main(int argc, char** argv) try {
    if (argc != 3) throw std::runtime_error("usage: harness stimulus result");
    DefaultMemoryValidator validator;
    Memory memory(validator, 0x40000, 0x40000, 0x40000);
    HarnessPeripherals px('X'), py('Y');
    DSP dsp(memory, &px, &py);
    Recorder rec; rec.dsp = &dsp; px.rec = &rec; py.rec = &rec;
    g_memoryAccessHookContext = &rec; g_memoryAccessHook = &Recorder::hook;
    g_instructionHook = &instructionHook;
    std::ifstream stimulus(argv[1]); if (!stimulus) throw std::runtime_error("missing stimulus");
    std::ofstream out(argv[2]);
    std::ofstream dump(std::string(argv[2]) + ".dump", std::ios::binary);
    std::string line; unsigned block = 0; bool shim = true; TWord entry = 0x2200;
    TWord callerSR = 0xe000d4; std::vector<TWord> caller(sizeof(CALLER_REGS) / sizeof(CALLER_REGS[0]), 0);
    std::map<TWord, std::vector<int64_t>> watch;  // pc -> [n, x0min,x0max,x1min,x1max,y0min,y0max,y1min,y1max, negmask]
    const TWord STUB = 0x0200, SENTINEL = 0x0202;
    TWord vecAt = 0x10000;
    std::vector<PokeTrigger> triggers;
    auto poke = [&](EMemArea a, TWord addr, TWord v) { rec.on = false; memory.set(a, addr, v); };
    auto installStubs = [&]() {
        poke(MemArea_P, STUB, 0x0bf080); poke(MemArea_P, STUB + 1, 0x0224);    // host caller: jsr P:0224
        poke(MemArea_P, SENTINEL, 0x000000);
        poke(MemArea_P, 0x0224, 0x0bf080); poke(MemArea_P, 0x0225, shim ? 0x2002 : entry);
        poke(MemArea_P, 0x0226, 0x00000c);                                      // host continues: rts
        poke(MemArea_P, 0x0367, 0x00000c);                                      // host mixer stub: rts
        poke(MemArea_P, 0x0102, 0x000000);                                      // startup continuation sentinel
    };
    auto observe = [&](TWord pc) {
        auto it = watch.find(pc); if (it == watch.end()) return;
        auto& v = it->second; ++v[0];
        const EReg regs[4] = {Reg_X0, Reg_X1, Reg_Y0, Reg_Y1};
        for (int i = 0; i < 4; ++i) {
            const TWord w = getReg(dsp, regs[i]);
            const int64_t s = (w & 0x800000) ? int64_t(w) - 0x1000000 : int64_t(w);
            if (v[0] == 1 || s < v[1 + 2 * i]) v[1 + 2 * i] = s;
            if (v[0] == 1 || s > v[2 + 2 * i]) v[2 + 2 * i] = s;
            if (s < 0) v[9] |= (1 << i);
        }
    };
    while (std::getline(stimulus, line)) {
        std::istringstream row(line); std::string op; row >> op;
        if (op.empty() || op[0] == '#') continue;
        if (op == "LOAD") {
            std::string o, path; row >> o >> path; const TWord origin = num(o);
            const auto words = readWords(path);
            for (size_t i = 0; i < words.size(); ++i) poke(MemArea_P, origin + i, words[i]);
        } else if (op == "MODE") {
            std::string m; row >> m;
            if (m == "SHIM") shim = true; else if (m == "DIRECT") { std::string e; row >> e; shim = false; entry = num(e); }
            else throw std::runtime_error("bad mode");
            installStubs();
        } else if (op == "X" || op == "Y" || op == "P") {
            std::string a, v; row >> a >> v;
            poke(op == "X" ? MemArea_X : op == "Y" ? MemArea_Y : MemArea_P, num(a), num(v));
        } else if (op == "D0") { std::string v; row >> v; py.d0 = num(v);
        } else if (op == "SR") { std::string v; row >> v; callerSR = num(v);
        } else if (op == "CALLER") {
            for (auto& v : caller) { std::string s; row >> s; v = num(s); }
        } else if (op == "WATCH") { std::string p; row >> p; watch[num(p)] = std::vector<int64_t>(10, 0);
        } else if (op == "WLOG") { std::string lo, hi; row >> lo >> hi; rec.logRanges.emplace_back(num(lo), num(hi));
        } else if (op == "TRIGGER") {
            std::string p, a, addr, v; row >> p >> a >> addr >> v;
            const EMemArea area = a == "X" ? MemArea_X : a == "Y" ? MemArea_Y : MemArea_P;
            triggers.push_back(PokeTrigger{num(p), area, num(addr), num(v), false});
        } else if (op == "STARTUP") {
            dsp.regs().sp.var = 0; dsp.regs().sc.var = 0; dsp.setPC(0x2000); rec.on = true; unsigned steps = 0;
            while (dsp.getPC().toWord() != 0x0102 && steps < 1000) { dsp.execInterpreter(); ++steps; }
            rec.on = false;
            out << "STARTUP " << (dsp.getPC().toWord() == 0x0102) << ' ' << steps << '\n';
        } else if (op == "BLOCK") {
            dsp.regs().sp.var = 0; dsp.regs().sc.var = 0;
            for (size_t i = 0; i < caller.size(); ++i) setReg(dsp, CALLER_REGS[i], caller[i]);
            dsp.writeReg(Reg_SR, TReg24(callerSR)); dsp.setPC(STUB);
            uint64_t steps = 0, c0 = dsp.getCycles(), cEntry = 0, cReturn = 0, cMixer = 0, executed = 0;
            bool entered = false, returned = false, mixed = false, escaped = false, regsOk = true, srOk = true;
            TWord entrySP = 0, entrySC = 0, returnSP = 0, returnSC = 0, entrySR = 0, minSP = 99, maxSP = 0;
            const TWord engineEntry = shim ? 0x2180 : entry, engineExit = shim ? 0x2004 : 0x0367;
            g_onInstruction = [&](TWord pc) {
                if (++executed > 4000000) { std::cerr << "instruction bound exceeded\n"; std::exit(3); }
                if (!(pc < 0x400 || (pc >= 0x2000 && pc < 0x4000))) { std::cerr << "escaped to " << std::hex << pc << '\n'; std::exit(4); }
                if (pc < 0x4000) executedPC[pc] = 1;
                for (auto& t : triggers) {
                    if (t.fired || t.pc != pc) continue;
                    t.fired = true;
                    const auto hook = g_memoryAccessHook; g_memoryAccessHook = nullptr;
                    memory.set(t.area, t.addr, t.value);
                    g_memoryAccessHook = hook;
                }
                if (pc == engineEntry && !entered) {
                    entered = true; cEntry = dsp.getCycles(); entrySP = dsp.regs().sp.toWord();
                    entrySC = dsp.regs().sc.toWord(); entrySR = dsp.getSR().toWord();
                }
                if (entered && !returned) {
                    const TWord sp = dsp.regs().sp.toWord(); if (sp < minSP) minSP = sp; if (sp > maxSP) maxSP = sp;
                    observe(pc);
                }
                if (pc == engineExit && entered && !returned) {
                    returned = true; cReturn = dsp.getCycles();
                    returnSP = dsp.regs().sp.toWord(); returnSC = dsp.regs().sc.toWord();
                }
                if (pc == 0x0367 && !mixed) {
                    mixed = true; cMixer = dsp.getCycles();
                    for (size_t i = 0; i < caller.size(); ++i) if (getReg(dsp, CALLER_REGS[i]) != caller[i]) regsOk = false;
                    if (dsp.getSR().toWord() != callerSR) srOk = false;
                }
            };
            rec.on = true; rec.transport.clear(); rec.header.clear(); rec.wseq.clear();
            while (steps < 2000000 && dsp.getPC().toWord() != SENTINEL) { dsp.execInterpreter(); ++steps; }
            rec.on = false; g_onInstruction = nullptr;
            const bool done = dsp.getPC().toWord() == SENTINEL;
            out << "BLOCK " << block << " done=" << done << " entered=" << entered << " returned=" << returned
                << " mixed=" << mixed << " escaped=" << escaped << " instructions=" << executed
                << " engine_cycles=" << (cReturn - cEntry) << " to_mixer_cycles=" << (cMixer - c0)
                << " entry_sp=" << entrySP << " entry_sc=" << entrySC << " return_sp=" << returnSP
                << " return_sc=" << returnSC << " min_sp=" << minSP << " max_sp=" << maxSP << std::hex << " entry_sr=" << entrySR
                << std::dec << " premixer_regs=" << regsOk << " premixer_sr=" << srOk
                << " final_sp=" << dsp.regs().sp.toWord() << " transport_writes=";
            bool firstWrite = true;
            for (TWord a : rec.transport) { out << (firstWrite ? "" : ",") << std::hex << a << std::dec; firstWrite = false; }
            if (firstWrite) out << '-';
            out << " header_writes=";
            firstWrite = true;
            for (TWord a : rec.header) { out << (firstWrite ? "" : ",") << std::hex << a << std::dec; firstWrite = false; }
            if (firstWrite) out << '-';
            unsigned fired = 0;
            for (const auto& t : triggers) fired += t.fired;
            out << " triggers=" << fired << '/' << triggers.size();
            out << " wseq=";
            firstWrite = true;
            for (const auto& w : rec.wseq) {
                out << (firstWrite ? "" : ",") << std::hex << w.first << '@' << w.second << std::dec; firstWrite = false;
            }
            if (firstWrite) out << '-';
            out << '\n';
            triggers.clear();
            ++block;
        } else if (op == "DUMP") {
            std::string area, lo, hi; row >> area >> lo >> hi;
            const EMemArea a = area == "X" ? MemArea_X : area == "Y" ? MemArea_Y : MemArea_P;
            const TWord l = num(lo), h = num(hi);
            out << "DUMP " << area << ' ' << std::hex << l << ' ' << h << std::dec << '\n';
            rec.on = false;
            for (TWord addr = l; addr <= h; ++addr) {
                const TWord w = memory.get(a, addr);
                dump.put(char((w >> 16) & 255)); dump.put(char((w >> 8) & 255)); dump.put(char(w & 255));
            }
        } else if (op == "CALL") {
            // Synthetic JSR to a subroutine with the current register set; stops on return.
            std::string a; row >> a; const TWord target = num(a);
            poke(MemArea_P, 0x0300, 0x0bf080); poke(MemArea_P, 0x0301, target); poke(MemArea_P, 0x0302, 0);
            dsp.regs().sp.var = 0; dsp.regs().sc.var = 0;
            for (size_t i = 0; i < caller.size(); ++i) setReg(dsp, CALLER_REGS[i], caller[i]);
            dsp.writeReg(Reg_SR, TReg24(callerSR)); dsp.setPC(0x0300); uint64_t steps = 0; rec.on = true;
            while (dsp.getPC().toWord() != 0x0302 && steps < 100000) { dsp.execInterpreter(); ++steps; }
            rec.on = false;
            out << "CALL " << std::hex << target << std::dec << " returned=" << (dsp.getPC().toWord() == 0x0302)
                << " steps=" << steps << '\n';
        } else if (op == "VEC") {
            // VEC word sr x0 x1 y0 y1 a2 a1 a0 b2 b1 b0 : execute one instruction, print A and B.
            std::string s[12]; for (auto& x : s) row >> x;
            // A fresh address per vector: the interpreter caches the decode of each P address.
            const TWord at = vecAt; vecAt += 2;
            if (vecAt >= 0x3f000) throw std::runtime_error("too many vectors");
            poke(MemArea_P, at, num(s[0])); poke(MemArea_P, at + 1, 0);
            dsp.writeReg(Reg_SR, TReg24(num(s[1])));
            const EReg r[] = {Reg_X0, Reg_X1, Reg_Y0, Reg_Y1, Reg_A2, Reg_A1, Reg_A0, Reg_B2, Reg_B1, Reg_B0};
            for (int i = 0; i < 10; ++i) setReg(dsp, r[i], num(s[2 + i]));
            dsp.setPC(at); dsp.execInterpreter();
            out << "VEC" << std::hex;
            for (EReg q : {Reg_A2, Reg_A1, Reg_A0, Reg_B2, Reg_B1, Reg_B0}) out << ' ' << getReg(dsp, q);
            out << std::dec << '\n';
        } else throw std::runtime_error("unknown stimulus: " + op);
    }
    for (const auto& item : rec.counts) {
        const uint64_t k = item.first;
        out << "ACCESS " << "PXY"[(k >> 49) & 3] << ' ' << ((k >> 48) & 1) << std::hex << ' ' << ((k >> 24) & 0xffffff)
            << ' ' << (k & 0xffffff) << std::dec << ' ' << item.second << '\n';
    }
    for (auto* p : {&px, &py}) {
        for (const auto& e : p->reads) out << "PERIPH " << p->space << " R " << std::hex << e.first.first << ' ' << e.first.second << std::dec << ' ' << e.second << '\n';
        for (const auto& e : p->writes) out << "PERIPH " << p->space << " W " << std::hex << e.first.first << ' ' << e.first.second << std::dec << ' ' << e.second << '\n';
    }
    for (TWord a = 0; a < 0x4000;) {
        if (!executedPC[a]) { ++a; continue; }
        TWord b = a; while (b + 1 < 0x4000 && executedPC[b + 1]) ++b;
        out << "EXEC " << std::hex << a << ' ' << b << std::dec << '\n'; a = b + 1;
    }
    for (const auto& w : watch) {
        out << "WATCH " << std::hex << w.first << std::dec;
        for (auto v : w.second) out << ' ' << v;
        out << '\n';
    }
    return 0;
} catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 2; }
