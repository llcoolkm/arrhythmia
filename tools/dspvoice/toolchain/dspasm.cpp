// SPDX-License-Identifier: MIT
// Line assembler/disassembler over the pinned dsp56300 library.
// asm mode, stdin "ADDR<TAB>TEXT" -> "ADDR<TAB>w0 [w1]<TAB>len<TAB>disassembly", or "ERR<TAB>code<TAB>text".
// dis mode, stdin "ADDR<TAB>w0 [w1]" -> "ADDR<TAB>len<TAB>disassembly".
#include <iostream>
#include <sstream>
#include <string>
#include <iomanip>
#include "dsp56kEmu/assembler.h"
#include "dsp56kEmu/disasm.h"
#include "dsp56kEmu/opcodes.h"
using namespace dsp56k;
int main(int argc, char** argv) {
    Assembler as; Opcodes ops; Disassembler dis(ops);
    const bool dmode = argc > 1 && std::string(argv[1]) == "dis";
    std::string line; int bad = 0;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        const auto tab = line.find('\t');
        const unsigned addr = std::stoul(line.substr(0, tab), nullptr, 16);
        const std::string text = line.substr(tab + 1);
        TWord w[2] = {0, 0}; unsigned n = 0;
        if (dmode) { std::istringstream q(text); q >> std::hex >> w[0]; if (!(q >> std::hex >> w[1])) w[1] = 0; }
        else {
            const auto r = as.assemble(text.c_str());
            if (!r.success()) { std::cout << "ERR\t" << static_cast<int>(r.error) << '\t' << text << '\n'; ++bad; continue; }
            w[0] = r.word[0]; w[1] = r.word[1]; n = r.wordCount;
        }
        std::string d; const auto m = dis.disassemble(d, w[0], w[1], 0, 0, addr);
        std::cout << std::hex << std::setfill('0') << std::setw(6) << addr << '\t';
        if (!dmode) { for (unsigned i = 0; i < n; ++i) std::cout << std::setw(6) << w[i] << (i + 1 < n ? " " : ""); std::cout << '\t'; }
        std::cout << std::dec << m << '\t' << d << '\n';
    }
    return bad ? 1 : 0;
}
