# Contributing an engine

The engine catalogue lists reviewed engines for track 8. You can also install a local `.dse` file through the web loader without adding it to the catalogue. A static file check does not establish that DSP code behaves correctly. Install local files only when you trust their source.

Read the [engine interface](docs/user/engines-dev.md) before building an engine. Platform 1 uses engine classes 1 to 127; class 2 is reserved. Ask for a class allocation before submitting a new design. Experimental file classes 128 to 255 are refused by the standard host checker and web loader.

Submit a pull request with the `.dse` file hosted in this repository and its catalogue entry. External download URLs are not supported. Add its entry to `web/engines/index.json`. Each entry has exactly these fields: `name`, `file`, `sha256`, `size`, `kind`, `version`, `author`, `licence` and `status`. Use `community` for a submitted engine. Give the author and licence accurately; source, build instructions and test results are useful supporting material.

Include the assembled listing beside a new or updated `.dse` file, with the same basename and a `.lst` extension. The checker binds the listing words and addresses to the submitted engine body before applying the instruction-sequence rules. It does not independently disassemble the listing text; a maintainer must review that correspondence. Catalogue checks refuse deterministic forbidden-pattern findings. Unresolved execution-dependent rules remain for manual review; this catalogue rule does not change local-file admission or the firmware checks.

The checks validate the file, its digest, the 15,360-byte install limit and the class range. A new design needs an unallocated class in 1 to 127. The listing check applies the public instruction-sequence rules. These checks do not add an entry automatically: a maintainer must review it before merging.

Before submitting, run these author checks from the exported tools:

```sh
python3 -I tools/engine_catalogue.py web/engines/index.json
python3 -I tools/engine_file.py check3 myeng.dse
python3 -I tools/engine_file.py inspect3 myeng.dse
python3 -I tools/dsp/abi3/sequence_warning.py assembled.lst
python3 -I tools/module.py card engine-put --check myeng.dse
```

To check a submission against a separate base checkout, run its trusted checker from the candidate checkout:

```sh
python3 -I ../base/tools/engine_catalogue.py web/engines/index.json --repo-root . --base-dir ../base
```

If a pull request changes `.github/` or checker paths, a maintainer must re-run its checks from the trusted main branch; a green check from the changed workflow is not sufficient.

The base comparison checks new or changed files and entries, class allocation and matching listings. The workflow runs the base checker and reads candidate files as data; it does not run submitted source or DSP code.

A clean listing scan covers only the restrictions it can resolve. Review the remaining [instruction and conformance rules](docs/user/engines-dev.md#building-and-conformance) by hand. A pull request's automated checks validate its catalogue data and files; they do not qualify an engine on hardware or publish it. Catalogue additions are reviewed before merging.
