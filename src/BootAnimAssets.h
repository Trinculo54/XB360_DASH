#pragma once

#include <cstdint>
#include <string>
#include <vector>

// Loaders for the Xbox 360 boot animation's own data, lifted out of
// bootanim.xex rather than re-authored. Three files, all produced by the
// extractor in tools\bootanim_extract (re-run it if you edit anything):
//
//   curves.bin   the 666 animation curves and the 184 vector tracks that
//                reference them. This is the authored timeline -- 43 KB.
//   nodes.bin    the 29 animated node transforms already evaluated and
//                baked to matrices at a fixed rate. Redundant with
//                curves.bin, but it needs no evaluator and is exact by
//                construction, so it is the one to start from.
//   meshes.bin   the sphere, the X inlay and the X edge trim, dequantised
//                out of the .data section.
//
// Nothing here touches D3D except BootMeshes::CreateBuffers, so the curve
// and node math can be unit-tested off-device.

// ---------------------------------------------------------------------------
//  Curves
// ---------------------------------------------------------------------------
// Each curve is a list of keys carrying a time, a value and two tangent
// handles stored as (dt, dv) vectors. There are two segment types and which
// one a curve uses is a flag in the file, not something inferable from the
// keys:
//
//   plain   the handles are read purely as slopes and the segment is a cubic
//           Hermite in real time. This is 654 of the 666 curves.
//   bezier  the segment is a cubic Bezier in the (time, value) plane, so the
//           curve parameter has to be solved for before the value can be
//           read. 12 curves, and they look visibly wrong if evaluated as
//           Hermite -- the flag matters.
//
// Both paths were checked against the console's own evaluator running under
// a recompiled-PPC harness: 133,866 samples across every curve, worst
// relative error 3.1e-06, which is float32 rounding.
class BootCurves
{
public:
    struct Key { float t, v, inDt, inDv, outDt, outDv; };

    bool Load(const std::string& path);
    bool IsLoaded() const { return !m_splines.empty(); }

    int  SplineCount() const { return (int)m_splines.size(); }
    int  TrackCount()  const { return (int)m_tracks.size(); }

    // Value of one curve at time t (seconds). Times outside the key range
    // clamp to the first/last value, which is what the title does.
    float Eval(int spline, float t) const;

    // A track is three curves plus a constant fallback per component; a
    // component with no curve holds its default for the whole animation.
    void EvalTrack(int track, float t, float outXYZ[3]) const;

private:
    struct Spline { uint32_t firstKey; uint16_t keyCount; uint8_t bezier; uint8_t pad; };
    struct Track  { float dflt[3]; int16_t spline[3]; };

    std::vector<Spline> m_splines;
    std::vector<Key>    m_keys;
    std::vector<Track>  m_tracks;
};

// ---------------------------------------------------------------------------
//  Baked node transforms
// ---------------------------------------------------------------------------
// 29 nodes, each a row-major 4x4, sampled at a fixed rate over the whole
// animation including the overrun past 6 s. Sampling was done by calling the
// title's own Curve_Evaluate, so no assumption about how it composes pivot,
// rotation and scale had to be made or can be wrong.
//
// Matrices are row-major with translation in row 3, the layout D3DX and
// stock HLSL mul(v, M) expect. Between samples this lerps componentwise --
// fine at 60 Hz for rotations this slow, and the alternative (proper slerp)
// would need the rotation decomposed back out.
class BootNodes
{
public:
    bool Load(const std::string& path);
    bool IsLoaded() const { return m_nodeCount > 0; }

    uint32_t NodeCount() const { return m_nodeCount; }
    float    Duration()  const { return m_duration; }

    // Writes 16 floats. Times past the end hold the final frame.
    void GetMatrix(uint32_t node, float t, float out[16]) const;

private:
    uint32_t m_nodeCount = 0;
    uint32_t m_frames    = 0;
    float    m_fps       = 60.0f;
    float    m_duration  = 0.0f;
    std::vector<float> m_data;   // frames * nodes * 16
};

// ---------------------------------------------------------------------------
//  Baked per-frame draw matrices
// ---------------------------------------------------------------------------
// The simplest of the three files and the one the renderer actually runs on.
// For each frame it holds four row-major 4x4 matrices:
//
//   0  orb world-view-projection      2  backdrop world-view-projection
//   1  orb world                      3  backdrop world
//
// They were captured by running the title's own Init and RenderFrame at each
// time step and recording what it handed to SetVertexShaderConstantF, so the
// node hierarchy, the curve evaluation and the camera are all already folded
// in. The orb is identity (i.e. not drawn) before t = 3.167 s, which is when
// the title first submits it -- and 3.16667 is a constant in .rdata, so that
// is the animation's own number rather than an artefact of the capture.
//
// Convention: clip = (dot(p,m[0]), dot(p,m[1]), dot(p,m[2]), dot(p,m[3]))
// with p = float4(position, 1). In HLSL that is mul(M, p) with M as loaded,
// or mul(p, transpose(M)).
class BootFrames
{
public:
    bool Load(const std::string& path);
    bool IsLoaded() const { return m_frames > 0; }
    float Duration() const { return m_duration; }

    // Nearest-frame lookup. The capture rate is the animation's own, so this
    // does not interpolate -- a frame either exists or the animation has not
    // started/has ended.
    const float* Matrices(float t) const;      // 4 * 16 floats, or null
    bool OrbVisible(float t) const;

private:
    uint32_t m_frames = 0, m_matsPerFrame = 4;
    float m_fps = 60.0f, m_duration = 0.0f;
    std::vector<float> m_data;
};

// ---------------------------------------------------------------------------
//  Meshes
// ---------------------------------------------------------------------------
// Positions are unit-radius: the title places and scales them with the node
// matrices above, so do not pre-scale them here. Indices describe one long
// triangle strip per mesh, the same primitive the original draws, degenerate
// triangles and all -- D3D11 handles those correctly with
// D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP and no special casing.
//
// Normals are not in the original data -- the title fills them with (0,0,1)
// and recomputes them at load. The packer does that same job offline,
// accumulating face normals along the strip and orienting them outward, so
// what arrives here is ready to draw.
class BootMeshes
{
public:
    struct Vertex { float px, py, pz; float nx, ny, nz; float u, v; };
    struct Mesh
    {
        std::string          name;
        std::vector<Vertex>  vertices;
        std::vector<uint16_t> indices;
        bool                 hasUV = false;
    };

    bool Load(const std::string& path);
    bool IsLoaded() const { return !m_meshes.empty(); }

    size_t      Count() const { return m_meshes.size(); }
    const Mesh& At(size_t i) const { return m_meshes[i]; }

private:
    std::vector<Mesh> m_meshes;
};
