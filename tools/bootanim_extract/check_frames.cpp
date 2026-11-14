// Loads the packed assets through the shipping loaders, off-device, and
// checks the things the renderer relies on.
#include "BootAnimAssets.h"
#include <cstdio>
#include <cmath>
int main()
{
    BootFrames f; BootMeshes m;
    if (!f.Load("frames.bin")) { std::printf("frames.bin FAILED to load\n"); return 1; }
    if (!m.Load("meshes.bin")) { std::printf("meshes.bin FAILED to load\n"); return 1; }
    std::printf("frames.bin  duration %.3f s\n", f.Duration());
    float firstVisible = -1.0f;
    for (float t = 0.0f; t < f.Duration(); t += 1.0f/60.0f)
        if (f.OrbVisible(t)) { firstVisible = t; break; }
    std::printf("orb first visible at t = %.3f s  (title constant is 3.16667)  %s\n",
                firstVisible, std::fabs(firstVisible - 3.16667f) < 0.02f ? "MATCH" : "MISMATCH");
    const float* mats = f.Matrices(5.0f);
    std::printf("orb wvp @5.0s row0: %.4f %.4f %.4f %.4f\n", mats[0], mats[1], mats[2], mats[3]);
    bool normOK = true; size_t checked = 0;
    for (size_t i = 0; i < m.Count(); ++i) {
        const BootMeshes::Mesh& mesh = m.At(i);
        for (const auto& v : mesh.vertices) {
            float l = std::sqrt(v.nx*v.nx + v.ny*v.ny + v.nz*v.nz);
            if (std::fabs(l - 1.0f) > 1e-3f) { normOK = false; break; }
            ++checked;
        }
        std::printf("mesh %-10s %5zu verts  %6zu idx\n", mesh.name.c_str(), mesh.vertices.size(), mesh.indices.size());
    }
    std::printf("normals unit length on all %zu vertices: %s\n", checked, normOK ? "yes" : "NO");
    return (firstVisible > 0 && normOK) ? 0 : 2;
}
