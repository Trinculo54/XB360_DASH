# bootanim_extract

Makes `assets\bootanim\`, which the app's boot animation
(`src\BootAnimation.cpp`) plays: `trace.bin`, a recording of every Direct3D
call the recompiled `bootanim.xex` makes over its 6.5 seconds, and the title's
own 17 shaders compiled for Direct3D 11. Neither is in the repository, so this
is how to make them.

## What it needs

* the XenonRecomp output for bootanim (`ppc/ppc_recomp.*.cpp`,
  `ppc_func_mapping.cpp`, `ppc_context.h`, `ppc_config.h`)
* XenosRecomp's HLSL translation of the title's shaders
* `bootanim_noaudio.xex` — the uncompressed XEX the recompiler was run on
* [simde](https://github.com/simd-everywhere/simde) — `ppc_context.h` includes
  `x86/avx.h` etc. from it
* clang or gcc, C++20 (the runtime uses `__builtin_assume`)
* `fxc` (the Windows SDK) and Python, for the shaders

## The recording

`capture.cpp` runs the title's own init, tick and frame routines and takes the
calls they make into its D3D (see the comment at its top for which functions
those are).

```sh
git clone --depth 1 https://github.com/simd-everywhere/simde.git
for f in ppc/ppc_recomp.*.cpp ppc/ppc_func_mapping.cpp; do
    clang++ -std=c++20 -O1 -w -c "$f" -Ippc -Isimde/simde -o "$(basename $f .cpp).o" &
done; wait
clang++ -std=c++20 -O1 -w -c runtime.cpp -Ippc -Isimde/simde -o runtime.o
clang++ -std=c++20 -O1 -w -c capture.cpp -Ippc -Isimde/simde -o capture.o
clang++ -o capture capture.o runtime.o ppc_recomp.*.o ppc_func_mapping.o

./capture bootanim_noaudio.xex trace.bin 60 6.5     # 391 frames, about 83 MB
./capture bootanim_noaudio.xex --text 5.0           # one frame's calls, as text
```

The whole recompiled title compiles in about 15 seconds in parallel. The
recording is the same file every run.

On Windows, with Visual Studio's clang (`VC\Tools\Llvm\x64\bin\clang++.exe`,
from a prompt that has run `vcvars64.bat`), add
`--target=x86_64-pc-windows-msvc -D__restrict= -D__restrict__=` to every
compile, the recompiled sources included: MSVC's ABI puts `__restrict` in a
function's mangled name, and the recompiler's declarations and definitions do
not agree about it.

## The shaders

`shaders.py` turns XenosRecomp's translation of the title's shaders into HLSL
that `fxc` compiles, and compiles it:

```sh
python shaders.py <XenosRecomp hlsl dir> ..\..\assets\bootanim    # shaderNN.vs.cso / .ps.cso
```

## How it works

The recompiled code needs four things from a host, and `runtime.cpp` is all
four:

1. **A guest address space.** 4 GiB (`mmap` with `MAP_NORESERVE`, or
   `VirtualAlloc`), the XEX basefile copied in at 0x98000000. This XEX is
   stored uncompressed and unencrypted, so the basefile is already in image
   layout and maps one-for-one.
2. **The register save/restore helpers.** XenonRecomp leaves `__savegprlr_N`
   and friends to the host. `savehelpers.inc` implements all 72 from the
   actual instruction bytes at 0x98043970 / 0x980439C0 / 0x98043CC0 / 0x98043D0C.
3. **Import stubs** (`import_stubs.inc`). Each aborts on contact, so a call
   the tool has no answer for is caught rather than guessed at. They are
   weak, and `capture.cpp` defines the six the title does reach: the video
   mode (a 720p widescreen display), the random numbers three generated
   textures are made from (a fixed sequence), an event and the critical
   sections.
4. **An allocator.** `sub_98021018` is the title's `operator new`. XenonRecomp
   emits it as a weak symbol, so defining it here overrides it and allocation
   never has to go through the guest CRT — which would otherwise need heap
   and TLS brought up first.

`capture.cpp` is the title's Direct3D in the same way: each D3D function the
title calls is a weak `sub_X`, and the tool's definition records the call.
