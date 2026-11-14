cmake_minimum_required(VERSION 3.20)
project(DashX360Native CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if (NOT WIN32)
    message(WARNING "DashX360Native targets Windows (Win32 + Direct3D11). "
                     "Configuring anyway so IDEs/cross tooling can index the sources, "
                     "but this will not produce a runnable app off-Windows.")
endif()

add_executable(DashX360Native WIN32
    src/main.cpp
    src/Window.cpp
    src/Window.h
    src/Renderer.cpp
    src/Renderer.h
    src/QuadRenderer.cpp
    src/QuadRenderer.h
    src/Shaders.h
    src/TextureLoader.cpp
    src/TextureLoader.h
    src/FontAtlas.cpp
    src/FontAtlas.h
    src/Scene.cpp
    src/Gamepad.cpp
    src/Navigator.cpp
    src/Guide.cpp
    src/Guide.h
    src/Scene.h
    src/SceneManifest.cpp
    src/SceneManifest.h
    src/FileUtil.cpp
    src/FileUtil.h
    src/AudioEngine.cpp
    src/AudioEngine.h
    src/VideoPlayer.cpp
    src/VideoPlayer.h
    src/BootAnimation.cpp
    src/BootAnimation.h
    src/BootAnimAssets.cpp
    src/BootAnimAssets.h
    third_party/stb_truetype.h
    third_party/miniaudio.h
)

# miniaudio is one 95k-line header and the ONLY translation unit that
# instantiates it is AudioEngine.cpp (MINIAUDIO_IMPLEMENTATION lives there).
# It compiles clean but noisily under /W4 -- it is not our code and there is
# nothing to act on in its warnings, so they are turned off for that file
# alone rather than for the project.
if (MSVC)
    set_source_files_properties(src/AudioEngine.cpp PROPERTIES COMPILE_OPTIONS "/W0")
else()
    set_source_files_properties(src/AudioEngine.cpp PROPERTIES COMPILE_OPTIONS "-w")
endif()

target_include_directories(DashX360Native PRIVATE src)

if (WIN32)
    # d3dcompiler: runtime HLSL compilation (QuadRenderer). windowscodecs +
    # ole32: WIC PNG decoding (TextureLoader) and the CoInitializeEx/
    # CoCreateInstance calls it needs.
    # miniaudio reaches WASAPI through COM and the multimedia device APIs;
    # ole32 was already here for WIC, and it needs no other import library --
    # everything else it uses it resolves at runtime itself.
    # mfplat/mfreadwrite/mfuuid: the boot video's H.264 decode through Media
    # Foundation's IMFSourceReader (VideoPlayer). In-box since Windows 7, no
    # redistributable, same reasoning as WIC for PNGs.
    target_link_libraries(DashX360Native PRIVATE d3d11 dxgi d3dcompiler dxguid windowscodecs ole32
        mfplat mfreadwrite mfuuid)
endif()

target_compile_definitions(DashX360Native PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
)

# Cooked scenes (manifest.txt + PNGs, see xur_cook.py) and reconstructed
# fonts live in assets\ at the project root and need to sit next to the
# built .exe at runtime -- main.cpp resolves them relative to the
# executable's own directory (see FileUtil::GetExecutableDir), not the
# project source tree, since the app doesn't know where it was built from.
if (EXISTS "${CMAKE_SOURCE_DIR}/assets")
    add_custom_command(TARGET DashX360Native POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            "${CMAKE_SOURCE_DIR}/assets"
            "$<TARGET_FILE_DIR:DashX360Native>/assets"
        COMMENT "Copying assets\\ next to the built executable"
    )
endif()
