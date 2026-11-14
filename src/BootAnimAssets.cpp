#include "BootAnimAssets.h"

#include <cstdio>
#include <cstring>
#include <cmath>
#include <algorithm>

namespace {

// The extractor writes big-endian, matching the console's own byte order, so
// the files can be diffed against the original .data blobs without a
// conversion step in between. These undo that on load.
uint32_t RdU32(const uint8_t* p) { return (uint32_t)p[0]<<24 | (uint32_t)p[1]<<16 | (uint32_t)p[2]<<8 | p[3]; }
uint16_t RdU16(const uint8_t* p) { return (uint16_t)((uint16_t)p[0]<<8 | p[1]); }
int16_t  RdS16(const uint8_t* p) { return (int16_t)RdU16(p); }
float    RdF32(const uint8_t* p) { uint32_t v = RdU32(p); float f; std::memcpy(&f, &v, 4); return f; }

bool ReadWholeFile(const std::string& path, std::vector<uint8_t>& out)
{
    FILE* f = nullptr;
#ifdef _WIN32
    if (fopen_s(&f, path.c_str(), "rb") != 0 || !f) return false;
#else
    f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
#endif
    std::fseek(f, 0, SEEK_END);
    long n = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    if (n <= 0) { std::fclose(f); return false; }
    out.resize((size_t)n);
    size_t got = std::fread(out.data(), 1, out.size(), f);
    std::fclose(f);
    return got == out.size();
}

float BezierAt(float p0, float p1, float p2, float p3, float u)
{
    float m = 1.0f - u;
    return m*m*m*p0 + 3.0f*m*m*u*p1 + 3.0f*m*u*u*p2 + u*u*u*p3;
}

} // namespace

// ---------------------------------------------------------------------------
//  BootCurves
// ---------------------------------------------------------------------------
bool BootCurves::Load(const std::string& path)
{
    std::vector<uint8_t> buf;
    if (!ReadWholeFile(path, buf)) return false;
    if (buf.size() < 20 || std::memcmp(buf.data(), "BACURVE1", 8) != 0) return false;

    const uint8_t* p = buf.data() + 8;
    uint32_t splineCount = RdU32(p + 0);
    uint32_t keyCount    = RdU32(p + 4);
    uint32_t trackCount  = RdU32(p + 8);
    p += 12;

    const size_t need = (size_t)splineCount*8 + (size_t)keyCount*24 + (size_t)trackCount*20;
    if (buf.size() < 20 + need) return false;

    m_splines.resize(splineCount);
    for (uint32_t i = 0; i < splineCount; ++i, p += 8)
        m_splines[i] = { RdU32(p), RdU16(p + 4), p[6], 0 };

    m_keys.resize(keyCount);
    for (uint32_t i = 0; i < keyCount; ++i, p += 24)
        m_keys[i] = { RdF32(p), RdF32(p+4), RdF32(p+8), RdF32(p+12), RdF32(p+16), RdF32(p+20) };

    m_tracks.resize(trackCount);
    for (uint32_t i = 0; i < trackCount; ++i, p += 20) {
        Track& t = m_tracks[i];
        t.dflt[0] = RdF32(p); t.dflt[1] = RdF32(p+4); t.dflt[2] = RdF32(p+8);
        t.spline[0] = RdS16(p+12); t.spline[1] = RdS16(p+14); t.spline[2] = RdS16(p+16);
    }
    return true;
}

float BootCurves::Eval(int spline, float t) const
{
    if (spline < 0 || spline >= (int)m_splines.size()) return 0.0f;
    const Spline& s = m_splines[spline];
    if (s.keyCount == 0) return 0.0f;

    const Key* k = &m_keys[s.firstKey];
    if (t <= k[0].t)              return k[0].v;
    if (t >= k[s.keyCount-1].t)   return k[s.keyCount-1].v;

    uint16_t i = 0;
    while (i + 1u < s.keyCount && k[i+1].t < t) ++i;
    const Key& a = k[i];
    const Key& b = k[i+1];

    if (!s.bezier) {
        // The common case. sub_98014E48 folds the pair into a cubic in real
        // time: the handles are read as slopes only, which is why their time
        // components appear here just as a divisor.
        float D  = b.t - a.t;
        if (D == 0.0f) return a.v;
        float DY = b.v - a.v;
        float m0 = (a.outDt != 0.0f) ? a.outDv / a.outDt : 0.0f;
        float m1 = (b.inDt  != 0.0f) ? b.inDv  / b.inDt  : 0.0f;

        float c3 = (m0*D + m1*D - 2.0f*DY) / (D*D*D);
        float c2 = (3.0f*DY - 2.0f*m0*D - m1*D) / (D*D);
        float d  = t - a.t;
        return ((c3*d + c2)*d + m0)*d + a.v;
    }

    // The 12 curves whose handles are genuinely asymmetric in time. Time is
    // not linear in the parameter here, so the parameter has to be recovered
    // first. Newton with a bisection guard: the guest solves the cubic
    // analytically, this converges to float precision in a few steps and is
    // a fraction of the code.
    const float k3 = 1.0f/3.0f;
    float x0 = a.t,               y0 = a.v;
    float x1 = a.t + a.outDt*k3,  y1 = a.v + a.outDv*k3;
    float x2 = b.t - b.inDt *k3,  y2 = b.v - b.inDv *k3;
    float x3 = b.t,               y3 = b.v;

    float lo = 0.0f, hi = 1.0f;
    float u  = (x3 > x0) ? (t - x0) / (x3 - x0) : 0.0f;
    for (int n = 0; n < 40; ++n) {
        float x = BezierAt(x0, x1, x2, x3, u) - t;
        if (std::fabs(x) < 1e-8f) break;
        if (x > 0.0f) hi = u; else lo = u;
        float m  = 1.0f - u;
        float dx = 3.0f*m*m*(x1-x0) + 6.0f*m*u*(x2-x1) + 3.0f*u*u*(x3-x2);
        float nu = (std::fabs(dx) > 1e-9f) ? u - x/dx : 0.5f*(lo+hi);
        u = (nu > lo && nu < hi) ? nu : 0.5f*(lo+hi);
    }
    return BezierAt(y0, y1, y2, y3, u);
}

void BootCurves::EvalTrack(int track, float t, float outXYZ[3]) const
{
    if (track < 0 || track >= (int)m_tracks.size()) { outXYZ[0] = outXYZ[1] = outXYZ[2] = 0.0f; return; }
    const Track& tr = m_tracks[track];
    for (int c = 0; c < 3; ++c)
        outXYZ[c] = (tr.spline[c] >= 0) ? Eval(tr.spline[c], t) : tr.dflt[c];
}

// ---------------------------------------------------------------------------
//  BootNodes
// ---------------------------------------------------------------------------
bool BootNodes::Load(const std::string& path)
{
    std::vector<uint8_t> buf;
    if (!ReadWholeFile(path, buf)) return false;
    if (buf.size() < 24 || std::memcmp(buf.data(), "BANODES1", 8) != 0) return false;

    // This header is written by the baker with plain fwrite, so it is host
    // (little-endian) order, unlike the curve and mesh payloads.
    std::memcpy(&m_nodeCount, buf.data() + 8,  4);
    std::memcpy(&m_frames,    buf.data() + 12, 4);
    std::memcpy(&m_fps,       buf.data() + 16, 4);
    std::memcpy(&m_duration,  buf.data() + 20, 4);

    const size_t count = (size_t)m_frames * m_nodeCount * 16;
    if (buf.size() < 24 + count*4 || m_nodeCount == 0 || m_frames == 0) { m_nodeCount = 0; return false; }
    m_data.resize(count);
    std::memcpy(m_data.data(), buf.data() + 24, count * 4);
    return true;
}

void BootNodes::GetMatrix(uint32_t node, float t, float out[16]) const
{
    if (node >= m_nodeCount || m_frames == 0) {
        std::memset(out, 0, 16 * sizeof(float));
        out[0] = out[5] = out[10] = out[15] = 1.0f;
        return;
    }
    float  fpos = t * m_fps;
    if (fpos < 0.0f) fpos = 0.0f;
    uint32_t f0 = (uint32_t)fpos;
    if (f0 >= m_frames - 1) {
        const float* m = &m_data[((size_t)(m_frames-1) * m_nodeCount + node) * 16];
        std::memcpy(out, m, 16 * sizeof(float));
        return;
    }
    float a = fpos - (float)f0;
    const float* m0 = &m_data[((size_t)f0     * m_nodeCount + node) * 16];
    const float* m1 = &m_data[((size_t)(f0+1) * m_nodeCount + node) * 16];
    for (int i = 0; i < 16; ++i) out[i] = m0[i] + (m1[i] - m0[i]) * a;
}

// ---------------------------------------------------------------------------
//  BootFrames
// ---------------------------------------------------------------------------
bool BootFrames::Load(const std::string& path)
{
    std::vector<uint8_t> buf;
    if (!ReadWholeFile(path, buf)) return false;
    if (buf.size() < 24 || std::memcmp(buf.data(), "BAFRAME1", 8) != 0) return false;
    std::memcpy(&m_frames,       buf.data() + 8,  4);
    std::memcpy(&m_matsPerFrame, buf.data() + 12, 4);
    std::memcpy(&m_fps,          buf.data() + 16, 4);
    std::memcpy(&m_duration,     buf.data() + 20, 4);
    const size_t count = (size_t)m_frames * m_matsPerFrame * 16;
    if (m_frames == 0 || buf.size() < 24 + count*4) { m_frames = 0; return false; }
    m_data.resize(count);
    std::memcpy(m_data.data(), buf.data() + 24, count*4);
    return true;
}

const float* BootFrames::Matrices(float t) const
{
    if (m_frames == 0) return nullptr;
    long f = (long)(t * m_fps + 0.5f);
    if (f < 0) f = 0;
    if (f >= (long)m_frames) f = (long)m_frames - 1;
    return &m_data[(size_t)f * m_matsPerFrame * 16];
}

bool BootFrames::OrbVisible(float t) const
{
    const float* m = Matrices(t);
    if (!m) return false;
    // Frames before the orb is submitted were written as identity.
    static const float ident[16] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
    for (int i = 0; i < 16; ++i) if (m[i] != ident[i]) return true;
    return false;
}

// ---------------------------------------------------------------------------
//  BootMeshes
// ---------------------------------------------------------------------------
bool BootMeshes::Load(const std::string& path)
{
    std::vector<uint8_t> buf;
    if (!ReadWholeFile(path, buf)) return false;
    if (buf.size() < 12 || std::memcmp(buf.data(), "BAMESH02", 8) != 0) return false;

    uint32_t count = RdU32(buf.data() + 8);
    const uint8_t* dir = buf.data() + 12;
    const size_t   dirSize = (size_t)count * 32;
    if (buf.size() < 12 + dirSize) return false;

    const uint8_t* blob = dir + dirSize;
    m_meshes.resize(count);
    for (uint32_t i = 0; i < count; ++i) {
        const uint8_t* e = dir + i*32;
        char name[17] = {};
        std::memcpy(name, e, 16);
        uint32_t verts = RdU32(e + 16);
        uint32_t idx   = RdU32(e + 20);
        uint32_t hasUV = RdU32(e + 24);
        uint32_t bytes = RdU32(e + 28);
        if ((size_t)(blob - buf.data()) + bytes > buf.size()) return false;

        Mesh& m = m_meshes[i];
        m.name  = name;
        m.hasUV = hasUV != 0;
        m.vertices.resize(verts);
        const uint8_t* p = blob;
        for (uint32_t v = 0; v < verts; ++v, p += 32)
            m.vertices[v] = { RdF32(p),    RdF32(p+4),  RdF32(p+8),
                              RdF32(p+12), RdF32(p+16), RdF32(p+20),
                              RdF32(p+24), RdF32(p+28) };
        m.indices.resize(idx);
        for (uint32_t k = 0; k < idx; ++k, p += 2)
            m.indices[k] = RdU16(p);
        blob += bytes;
    }
    return true;
}
