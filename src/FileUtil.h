#pragma once

#include <string>
#include <vector>
#include <cstdint>

// Small file/string helpers shared by the asset loaders (FontAtlas,
// TextureLoader, SceneManifest) so each one isn't reinventing "read a file
// by wide path" or "narrow string -> wide string" on its own.
namespace FileUtil
{
    // Reads a whole file via the Win32 API (CreateFileW/ReadFile) rather
    // than std::ifstream -- ifstream's std::wstring-path constructor is a
    // non-standard MSVC STL extension that MinGW's libstdc++ (used for this
    // project's cross-compile sanity checks) doesn't provide, so going
    // straight to Win32 keeps file reading correct under both toolchains.
    // Returns an empty vector on any failure (missing file, zero-length,
    // short read) -- callers should treat that as "asset unavailable", not
    // a crash.
    std::vector<uint8_t> ReadWholeFileBinary(const std::wstring& path);

    // Same, but returns the bytes as a std::string (manifest.txt and other
    // small text assets are plain ASCII, so no encoding conversion needed).
    bool ReadWholeFileText(const std::wstring& path, std::string& outText);

    // Converts an ASCII/UTF-8 narrow string to wide -- used for filenames
    // read out of manifest.txt (always plain ASCII, since they come from
    // the cook script's own PNG basenames) that need to become a
    // std::wstring for CreateFileW/WIC.
    std::wstring Utf8ToWide(const std::string& s);

    // The reverse -- used for scene folder names, which become the scene ids
    // that manifests reference in GOTO actions and cross-scene nav links.
    std::string WideToUtf8(const std::wstring& s);

    // Directory containing the running .exe, with a trailing backslash --
    // used to resolve asset paths (assets\fonts\..., assets\scenes\...)
    // relative to where the executable actually lives rather than the
    // process's current working directory, which varies by how the app was
    // launched (double-click, VS debugger, a shortcut) and isn't reliable.
    // CMakeLists.txt copies the assets\ folder next to the built .exe.
    std::wstring GetExecutableDir();
}
