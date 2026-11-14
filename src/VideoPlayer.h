#pragma once

#include <d3d11.h>
#include <wrl/client.h>

#include <cstdint>
#include <string>

// Plays one H.264 .mp4 into a D3D11 texture, a frame at a time, so the boot
// animation can be drawn through the same QuadRenderer as everything else
// rather than through a separate video window.
//
// Video only. The file's audio track is NOT decoded here -- it is extracted
// to a wav ahead of time (assets\sounds\boot.wav) and played through
// AudioEngine, which already exists and is already the thing that owns the
// output device. Decoding AAC here as well would mean a second audio path
// and a second thing to keep in sync with the first; two clocks started
// together on the same frame drift by less than that costs.
//
// Built on Media Foundation's IMFSourceReader, which is in-box from Windows
// 7 on and needs no redistributable -- the same reasoning that picked WIC
// for PNGs. The reader is asked for plain RGB32 frames and hands back
// system memory, which this class uploads to a dynamic texture. That is a
// copy per frame that a DXGI-manager setup would avoid, but it also works on
// any device regardless of whether it was created with video support, and
// 1920x1080 BGRA is 8MB/frame of PCIe traffic at a point in the app where
// nothing else is competing for it.
class VideoPlayer
{
public:
    ~VideoPlayer();
    VideoPlayer() = default;
    VideoPlayer(const VideoPlayer&) = delete;
    VideoPlayer& operator=(const VideoPlayer&) = delete;

    // Opens `path` and prepares the first frame's worth of resources.
    // Returns false if Media Foundation won't start, the file is missing, or
    // it holds no video stream -- all of which the caller should treat as
    // "no boot animation", not as fatal.
    bool Open(ID3D11Device* device, const std::wstring& path);

    // Decodes forward to whatever frame belongs at `elapsedSeconds` since the
    // video started, uploading it if it changed. Frames whose presentation
    // time has already passed are skipped rather than shown late, so a slow
    // machine drops frames instead of playing the boot in slow motion.
    // Returns false once the stream has ended.
    bool Update(ID3D11DeviceContext* context, float elapsedSeconds);

    // The most recently uploaded frame, or null before the first one has
    // been decoded. Valid to pass straight to QuadRenderer::DrawImage.
    ID3D11ShaderResourceView* GetFrameSRV() const { return m_srv.Get(); }

    bool IsOpen() const { return m_reader != nullptr; }
    bool IsFinished() const { return m_finished; }

    uint32_t GetWidth() const { return m_width; }
    uint32_t GetHeight() const { return m_height; }

    // Releases the decoder and the frame texture. Called by the destructor;
    // worth calling by hand the moment the boot finishes, since the reader
    // holds the whole file open and the texture is 8MB that the dashboard
    // proper has no use for.
    void Close();

private:
    // Media Foundation has to be started once per process and shut down the
    // same number of times. Tracked here rather than in main so that opening
    // and closing a player is self-contained.
    bool m_mfStarted = false;
    bool m_finished = false;

    struct IMFSourceReader* m_reader = nullptr;

    Microsoft::WRL::ComPtr<ID3D11Texture2D> m_texture;
    Microsoft::WRL::ComPtr<ID3D11ShaderResourceView> m_srv;

    uint32_t m_width = 0;
    uint32_t m_height = 0;
    int32_t m_stride = 0;      // bytes per row, always positive
    // Whether the decoded buffer stores the picture bottom-up. Taken only
    // from an explicitly negative MF_MT_DEFAULT_STRIDE -- see Open().
    bool m_bottomUp = false;

    // Presentation time of the frame currently in the texture, in seconds.
    // Negative means nothing has been decoded yet.
    double m_frameTime = -1.0;
};
