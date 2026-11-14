// ---------------------------------------------------------------------------
//  Runs Curves_Init headless and dumps the boot animation's curve dataset,
//  then checks a portable C++ evaluator against the guest's own Spline_Eval.
//
//  Curves_Init's only external dependency is operator new. The import stubs
//  in runtime.cpp abort on contact, so reaching the end means nothing else
//  was touched and the tables below are exactly what the console builds.
// ---------------------------------------------------------------------------
#include "ppc_config.h"
#include "ppc_context.h"
#include <cstdio>
#include <cstring>
#include <cmath>
#include <string>
#include <vector>
#include <algorithm>

extern uint8_t* g_base;
extern bool g_logAllocs;
bool RuntimeInit(const char* xexPath);
void SetupContext(PPCContext& ctx, uint32_t stackTop);
uint32_t GuestHeapUsed();
uint32_t GuestAlloc(uint32_t);

PPC_EXTERN_FUNC(sub_98016B48);   // Curves_Init
PPC_EXTERN_FUNC(sub_98014F88);   // Spline_Eval(spline, t) -> f1

static uint32_t GU32(uint32_t a){ return __builtin_bswap32(*(uint32_t*)(g_base+a)); }
static uint16_t GU16(uint32_t a){ return __builtin_bswap16(*(uint16_t*)(g_base+a)); }
static uint8_t  GU8 (uint32_t a){ return *(uint8_t*)(g_base+a); }
static float    GF32(uint32_t a){ uint32_t v=GU32(a); float f; std::memcpy(&f,&v,4); return f; }

static const uint32_t G_NODES = 0x98077970, G_TRACKS = 0x98077974, G_T978 = 0x98077978;
static const uint32_t G_T97C  = 0x9807797C, G_T980   = 0x98077980, G_SPLINES = 0x98077984;

// ---------------------------------------------------------------------------
//  Portable evaluator. Each segment is a cubic Bezier in (time, value): the
//  stored tangents are handle vectors spanning the segment, and the guest
//  divides them by three to make the two interior control points. Because x
//  is not linear in the parameter, the value at a given time needs the
//  parameter solved for first -- which is the same thing Maya, After Effects
//  and Blender all do with this curve type.
// ---------------------------------------------------------------------------
struct Key { float t, v, inDt, inDv, outDt, outDv; };
struct Spline { std::vector<Key> keys; bool bezier = false; };

// The two curve types the title uses. Which one a spline gets is a byte in
// its header, not something derivable from the keys, so it has to travel
// with the data.
//
//   plain (byte56 == 0): sub_98014E48 folds the two keys into a cubic in
//     real time -- the handles are read purely as slopes and their time
//     components only matter as a divisor. Three FMAs to evaluate.
//
//   bezier (byte56 != 0): the segment is a genuine cubic Bezier in the
//     (time, value) plane, so time is not linear in the curve parameter and
//     the parameter has to be recovered before the value can be read. The
//     title does it with an analytic cubic solve; Newton converges to float
//     precision here in a handful of iterations and is far less code.

static float BezierAt(float p0,float p1,float p2,float p3,float u)
{
    float m = 1.0f - u;
    return m*m*m*p0 + 3.0f*m*m*u*p1 + 3.0f*m*u*u*p2 + u*u*u*p3;
}

static float EvalSegmentBezier(const Key& a, const Key& b, float t)
{
    const float k = 1.0f/3.0f;
    float x0=a.t, x1=a.t + a.outDt*k, x2=b.t - b.inDt*k, x3=b.t;
    float y0=a.v, y1=a.v + a.outDv*k, y2=b.v - b.inDv*k, y3=b.v;

    float lo = 0.0f, hi = 1.0f;
    float u  = (x3 > x0) ? (t - x0) / (x3 - x0) : 0.0f;
    for (int i = 0; i < 40; ++i) {
        float x = BezierAt(x0,x1,x2,x3,u) - t;
        if (std::fabs(x) < 1e-8f) break;
        if (x > 0.0f) hi = u; else lo = u;
        float m  = 1.0f - u;
        float dx = 3.0f*m*m*(x1-x0) + 6.0f*m*u*(x2-x1) + 3.0f*u*u*(x3-x2);
        float nu = (std::fabs(dx) > 1e-9f) ? u - x/dx : 0.5f*(lo+hi);
        u = (nu > lo && nu < hi) ? nu : 0.5f*(lo+hi);   // bisect if Newton escapes
    }
    return BezierAt(y0,y1,y2,y3,u);
}

static float EvalSegment(const Key& a, const Key& b, float t)
{
    // sub_98014E48 turns the two keys into a cubic in real time. The stored
    // handles are (dt, dv) vectors, so each one is a slope directly -- there
    // is no Bezier parameter to solve for, which is why the guest can cache
    // four polynomial coefficients per segment and evaluate with three FMAs.
    float D  = b.t - a.t;
    float DY = b.v - a.v;
    float m0 = (a.outDt != 0.0f) ? a.outDv / a.outDt : 0.0f;   // start slope
    float m1 = (b.inDt  != 0.0f) ? b.inDv  / b.inDt  : 0.0f;   // end slope
    if (D == 0.0f) return a.v;

    float c3 = (m0*D + m1*D - 2.0f*DY) / (D*D*D);
    float c2 = (3.0f*DY - 2.0f*m0*D - m1*D) / (D*D);
    float c1 = m0;
    float c0 = a.v;
    float d  = t - a.t;
    return ((c3*d + c2)*d + c1)*d + c0;
}

static float EvalSpline(const Spline& s, float t)
{
    const std::vector<Key>& k = s.keys;
    if (k.empty()) return 0.0f;
    if (t <= k.front().t) return k.front().v;
    if (t >= k.back().t)  return k.back().v;
    size_t i = 0;
    while (i + 1 < k.size() && k[i+1].t < t) ++i;
    return s.bezier ? EvalSegmentBezier(k[i], k[i+1], t)
                    : EvalSegment      (k[i], k[i+1], t);
}

int main(int argc, char** argv)
{
    const char* xex = argc > 1 ? argv[1] : "bootanim_noaudio.xex";
    const char* out = argc > 2 ? argv[2] : "bootanim_curves.json";
    if (!RuntimeInit(xex)) return 1;

    PPCContext ctx;
    SetupContext(ctx, 0x70100000);
    std::printf("running Curves_Init headless...\n");
    sub_98016B48(ctx, g_base);
    std::printf("  ok - guest heap used %u bytes, no kernel import was reached\n\n", GuestHeapUsed());

    uint32_t splines = GU32(G_SPLINES), tracks = GU32(G_TRACKS), nodes = GU32(G_NODES);
    // The spline table is bounded by the next allocation (the track table),
    // which is how the count is known without hardcoding it.
    const int SPLINE_COUNT = (int)((tracks - splines) / 64);
    const int TRACK_COUNT  = (int)((GU32(G_T980) - tracks) / 20);
    const uint32_t nodeCount = nodes ? GU32(nodes - 4) : 0;

    int b56 = 0;
    std::vector<Spline> all(SPLINE_COUNT);
    int totalKeys = 0;
    for (int s = 0; s < SPLINE_COUNT; ++s) {
        uint32_t sp = splines + s*64;
        int n = (int8_t)GU8(sp + 57);
        if (GU8(sp + 56)) { ++b56; all[s].bezier = true; }
        uint32_t kp = GU32(sp + 60);
        for (int i = 0; i < n; ++i) {
            uint32_t k = kp + i*24;
            all[s].keys.push_back({ GF32(k), GF32(k+4), GF32(k+8), GF32(k+12), GF32(k+16), GF32(k+20) });
        }
        totalKeys += (n > 0 ? n : 0);
    }
    std::printf("splines %d   keys %d   tracks %d   nodes %u\n", SPLINE_COUNT, totalKeys, TRACK_COUNT, nodeCount);
    std::printf("splines using the alternate (byte56) evaluator: %d\n\n", b56);

    // ---- validate the portable evaluator against the guest's own ----------
    std::printf("checking portable evaluator against guest Spline_Eval...\n");
    double worst = 0.0; int worstSpline = -1; float worstT = 0; long samples = 0;
    std::vector<double> bad(SPLINE_COUNT, 0.0);
    for (int s = 0; s < SPLINE_COUNT; ++s) {
        if (all[s].keys.size() < 2) continue;
        float t0 = all[s].keys.front().t, t1 = all[s].keys.back().t;
        for (int i = 0; i <= 200; ++i) {
            float t = t0 + (t1 - t0) * (float)i / 200.0f;
            PPCContext c; SetupContext(c, 0x70100000);
            c.r3.u64 = splines + s*64;
            c.f1.f64 = (double)t;
            sub_98014F88(c, g_base);
            double guest = c.f1.f64;
            double mine  = EvalSpline(all[s], t);
            double err   = std::fabs(guest - mine);
            double scale = std::max(1.0, std::fabs(guest));
            if (err / scale > worst) { worst = err / scale; worstSpline = s; worstT = t; }
            if (err / scale > 1e-4) bad[s] = std::max(bad[s], err/scale);
            ++samples;
        }
    }
    int nbad = 0; for (int s = 0; s < SPLINE_COUNT; ++s) if (bad[s] > 0) ++nbad;
    std::printf("  %ld samples, worst relative error %.3e (spline %d at t=%.4f)\n", samples, worst, worstSpline, worstT);
    std::printf("  splines disagreeing: %d of %d\n", nbad, SPLINE_COUNT);
    for (int s = 0, shown = 0; s < SPLINE_COUNT && shown < 12; ++s) if (bad[s] > 0) {
        std::printf("    spline %3d  err %.3e  keys %zu  byte56=%d  times:", s, bad[s], all[s].keys.size(), GU8(splines+s*64+56));
        for (auto& k : all[s].keys) std::printf(" %.4f", k.t);
        std::printf("  vals:");
        for (auto& k : all[s].keys) std::printf(" %.4f", k.v);
        std::printf("\n    handles:");
        for (auto& k : all[s].keys) std::printf(" (in %.4f,%.4f out %.4f,%.4f)", k.inDt,k.inDv,k.outDt,k.outDv);
        std::printf("\n"); ++shown;
    }
    std::printf("\n");

    // ---- write JSON -------------------------------------------------------
    std::string j = "{\n";
    { char b[256]; std::snprintf(b,sizeof b,
        "  \"counts\": { \"splines\": %d, \"keys\": %d, \"tracks\": %d, \"nodes\": %u },\n"
        "  \"format\": \"key = [time, value, inHandleDt, inHandleDv, outHandleDt, outHandleDv]; cubic bezier in (time,value), handles scaled by 1/3\",\n",
        SPLINE_COUNT, totalKeys, TRACK_COUNT, nodeCount); j += b; }

    j += "  \"splines\": [\n";
    for (int s = 0; s < SPLINE_COUNT; ++s) {
        char b[256]; std::snprintf(b,sizeof b,"    { \"bezier\": %d, \"keys\": [", all[s].bezier?1:0); j += b;
        for (size_t i = 0; i < all[s].keys.size(); ++i) {
            const Key& k = all[s].keys[i];
            std::snprintf(b,sizeof b,"%s[%.9g,%.9g,%.9g,%.9g,%.9g,%.9g]", i?",":"",
                          k.t,k.v,k.inDt,k.inDv,k.outDt,k.outDv); j += b;
        }
        j += (s == SPLINE_COUNT-1) ? "] }\n" : "] },\n";
    }
    j += "  ],\n  \"tracks\": [\n";
    for (int t = 0; t < TRACK_COUNT; ++t) {
        uint32_t tp = tracks + t*20; char b[256];
        std::snprintf(b,sizeof b,"    { \"default\": [%.9g,%.9g,%.9g], \"splines\": [%d,%d,%d] }%s\n",
            GF32(tp),GF32(tp+4),GF32(tp+8),
            (int16_t)GU16(tp+12),(int16_t)GU16(tp+14),(int16_t)GU16(tp+16),
            t==TRACK_COUNT-1?"":","); j += b;
    }
    j += "  ],\n  \"nodes\": [\n";
    for (uint32_t n = 0; n < nodeCount; ++n) {
        uint32_t np = nodes + n*56; char b[512];
        std::snprintf(b,sizeof b,"    { \"trackA\": %d, \"trackB\": %d, \"raw\": \"", GU8(np), GU8(np+1)); j += b;
        for (int i = 0; i < 56; ++i) { std::snprintf(b,sizeof b,"%02X", GU8(np+i)); j += b; }
        j += (n==nodeCount-1) ? "\" }\n" : "\" },\n";
    }
    j += "  ]\n}\n";

    FILE* f = std::fopen(out,"wb"); std::fwrite(j.data(),1,j.size(),f); std::fclose(f);
    std::printf("wrote %s (%zu bytes)\n", out, j.size());
    return 0;
}
