// ---------------------------------------------------------------------------
//  Bakes the 29 animated node transforms by calling the title's own
//  Curve_Evaluate at each time step. Nothing about the node struct has to be
//  understood for this to be exact -- the guest composes the matrix and we
//  read it back.
// ---------------------------------------------------------------------------
#include "ppc_config.h"
#include "ppc_context.h"
#include <cstdio>
#include <cstring>
#include <cmath>
#include <vector>
#include <string>

extern uint8_t* g_base;
bool RuntimeInit(const char*);
void SetupContext(PPCContext&, uint32_t);
uint32_t GuestAlloc(uint32_t);

PPC_EXTERN_FUNC(sub_98016B48);   // Curves_Init
PPC_EXTERN_FUNC(sub_98015D58);   // Curve_Evaluate(node, t, outMatrix)

static uint32_t GU32(uint32_t a){ return __builtin_bswap32(*(uint32_t*)(g_base+a)); }
static float    GF32(uint32_t a){ uint32_t v=GU32(a); float f; std::memcpy(&f,&v,4); return f; }

int main(int argc, char** argv)
{
    const char* xex = argc > 1 ? argv[1] : "bootanim_noaudio.xex";
    const char* out = argc > 2 ? argv[2] : "bootanim_nodes.bin";
    const float  fps = argc > 3 ? (float)atof(argv[3]) : 60.0f;
    const float  dur = 6.5f;                        // covers the 6.467 s overrun
    const int    frames = (int)(dur * fps) + 1;
    if (!RuntimeInit(xex)) return 1;

    PPCContext ctx; SetupContext(ctx, 0x70100000);
    sub_98016B48(ctx, g_base);
    uint32_t nodes = GU32(0x98077970);
    uint32_t count = GU32(nodes - 4);
    uint32_t mat   = GuestAlloc(256);
    std::printf("baking %u nodes x %d frames @ %.0f Hz\n", count, frames, fps);

    std::vector<float> data;                        // frames * count * 16
    data.reserve((size_t)frames * count * 16);
    int identityNodes = 0, movingNodes = 0;
    std::vector<bool> moves(count, false);

    for (int f = 0; f < frames; ++f) {
        float t = (float)f / fps;
        for (uint32_t n = 0; n < count; ++n) {
            PPCContext c; SetupContext(c, 0x70100000);
            c.r3.u64 = nodes + n*56;
            c.f1.f64 = (double)t;
            c.r5.u64 = mat;
            sub_98015D58(c, g_base);
            for (int i = 0; i < 16; ++i) data.push_back(GF32(mat + i*4));
        }
    }
    // Which nodes actually animate? (compare frame 0 against every other)
    for (uint32_t n = 0; n < count; ++n) {
        for (int f = 1; f < frames && !moves[n]; ++f)
            for (int i = 0; i < 16; ++i)
                if (std::fabs(data[((size_t)f*count+n)*16+i] - data[(size_t)n*16+i]) > 1e-5f) { moves[n] = true; break; }
        moves[n] ? ++movingNodes : ++identityNodes;
    }
    std::printf("  %d nodes animate, %d stay put\n", movingNodes, identityNodes);

    // sanity: are the 3x3 blocks orthonormal (i.e. real rotations)?
    double worstOrtho = 0;
    for (size_t k = 0; k + 16 <= data.size(); k += 16) {
        const float* m = &data[k];
        for (int r = 0; r < 3; ++r) {
            double len = 0; for (int c2 = 0; c2 < 3; ++c2) len += (double)m[r*4+c2]*m[r*4+c2];
            worstOrtho = std::max(worstOrtho, std::fabs(std::sqrt(len) - 1.0));
        }
    }
    std::printf("  worst row-norm deviation from 1.0: %.3e (rotation matrices)\n", worstOrtho);

    FILE* fp = std::fopen(out, "wb");
    struct { char magic[8]; uint32_t nodes, frames; float fps, duration; } hdr =
        { {'B','A','N','O','D','E','S','1'}, count, (uint32_t)frames, fps, dur };
    std::fwrite(&hdr, sizeof hdr, 1, fp);
    std::fwrite(data.data(), sizeof(float), data.size(), fp);
    std::fclose(fp);
    std::printf("wrote %s (%zu bytes)\n", out, sizeof(hdr) + data.size()*4);

    std::printf("\nnode 0 at t=0.0 :"); for (int i=0;i<16;++i) std::printf(" %6.3f", data[i]);
    size_t off = (size_t)((int)(3.0f*fps)*count + 0)*16;
    std::printf("\nnode 0 at t=3.0 :"); for (int i=0;i<16;++i) std::printf(" %6.3f", data[off+i]);
    std::printf("\n");
    return 0;
}
