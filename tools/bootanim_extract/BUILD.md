# bootanim_extract

Runs pieces of the recompiled `bootanim.xex` headless to pull out the data
that `assets\bootanim\` is built from. You only need this if you want to
change something and re-derive the assets — the checked-in files are the
output of running it as-is.

## What it needs

* the XenonRecomp output for bootanim (`ppc/ppc_recomp.*.cpp`,
  `ppc_func_mapping.cpp`, `ppc_context.h`, `ppc_config.h`)
* `bootanim_noaudio.xex` — the uncompressed XEX the recompiler was run on
* [simde](https://github.com/simd-everywhere/simde) — `ppc_context.h` includes
  `x86/avx.h` etc. from it
* clang or gcc, C++20 for the recompiled sources (the runtime uses
  `__builtin_assume`), C++17 is enough for everything else

## Build

```sh
git clone --depth 1 https://github.com/simd-everywhere/simde.git
for f in ppc/ppc_recomp.*.cpp ppc/ppc_func_mapping.cpp; do
    clang++ -std=c++20 -O1 -w -c "$f" -Ippc -Isimde/simde -o "$(basename $f .cpp).o" &
done; wait
clang++ -std=c++20 -O1 -w -c runtime.cpp -Ippc -Isimde/simde -o runtime.o

clang++ -std=c++20 -O1 -w -c extract.cpp -Ippc -Isimde/simde -o extract.o
clang++ -o extract extract.o runtime.o ppc_recomp.*.o ppc_func_mapping.o

clang++ -std=c++20 -O1 -w -c bake.cpp -Ippc -Isimde/simde -o bake.o
clang++ -o bake bake.o runtime.o ppc_recomp.*.o ppc_func_mapping.o
```

The whole recompiled title compiles in about 15 seconds in parallel.

## Run

```sh
./extract bootanim_noaudio.xex bootanim_curves.json   # 666 curves, 184 tracks, 29 nodes
./bake    bootanim_noaudio.xex nodes.bin 120          # baked node matrices at 120 Hz
./textures bootanim_noaudio.xex                       # the five procedural textures
./verify  nodes.bin                                   # checks the packed files against the title
```

`verify` is the one that matters: it loads the packed assets through
`BootAnimAssets.cpp` — the same code the app uses — and compares against the
title's own `Spline_Eval` and `Curve_Evaluate` running in the harness.

## How it works

The recompiled code needs four things from a host, and `runtime.cpp` is all
four:

1. **A guest address space.** 4 GiB reserved with `MAP_NORESERVE`, the XEX
   basefile copied in at 0x98000000. This XEX is stored uncompressed and
   unencrypted, so the basefile is already in image layout and maps one-for-one.
2. **The register save/restore helpers.** XenonRecomp leaves `__savegprlr_N`
   and friends to the host. `savehelpers.inc` implements all 72 from the
   actual instruction bytes at 0x98043970 / 0x980439C0 / 0x98043CC0 / 0x98043D0C.
3. **Import stubs.** All 104 abort on contact. That is deliberate: the data
   entry points are supposed to touch none of them, and a trap means the
   assumption was wrong. `Curves_Init` reaches the end without hitting one.
4. **An allocator.** `sub_98021018` is the title's `operator new`. XenonRecomp
   emits it as a weak symbol, so defining it here overrides it and allocation
   never has to go through the guest CRT — which would otherwise need heap
   and TLS brought up first.

`textures.cpp` adds two more overrides, `sub_980268E0` (CreateTexture) and
`sub_980268C0` (LockRect), which hand back plain linear memory. That is all
the generators use them for: they compute every address themselves and only
ever read `pBits` out of the locked rect, never the pitch.
