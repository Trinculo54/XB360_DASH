#include "VideoPlayer.h"

#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <mferror.h>

#include <algorithm>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace
{
    // MF timestamps are in 100ns units.
    constexpr double kHnsPerSecond = 10000000.0;

    template <typename T>
    void SafeRelease(T*& p)
    {
        if (p)
        {
            p->Release();
            p = nullptr;
        }
    }
}

VideoPlayer::~VideoPlayer()
{
    Close();
}

void VideoPlayer::Close()
{
    SafeRelease(m_reader);
    m_srv.Reset();
    m_texture.Reset();
    if (m_mfStarted)
    {
        MFShutdown();
        m_mfStarted = false;
    }
    m_finished = true;
}

bool VideoPlayer::Open(ID3D11Device* device, const std::wstring& path)
{
    if (!device)
    {
        return false;
    }

    if (FAILED(MFStartup(MF_VERSION, MFSTARTUP_LITE)))
    {
        return false;
    }
    m_mfStarted = true;

    // ENABLE_ADVANCED_VIDEO_PROCESSING is what lets the reader hand back
    // RGB32 at all: H.264 decodes to NV12, and without this the reader
    // refuses any output type the decoder can't produce directly instead of
    // inserting a converter. With it, MF builds decoder -> converter for us.
    ComPtr<IMFAttributes> attributes;
    if (FAILED(MFCreateAttributes(&attributes, 2)))
    {
        Close();
        return false;
    }
    attributes->SetUINT32(MF_SOURCE_READER_ENABLE_ADVANCED_VIDEO_PROCESSING, TRUE);
    // The decoder may still use hardware internally; this only says we are
    // fine with it doing so.
    attributes->SetUINT32(MF_READWRITE_ENABLE_HARDWARE_TRANSFORMS, TRUE);

    if (FAILED(MFCreateSourceReaderFromURL(path.c_str(), attributes.Get(), &m_reader)))
    {
        Close();
        return false;
    }

    // Video only. Leaving the audio stream selected would have the reader
    // decode AAC we never read, which at best wastes the work and at worst
    // stalls the pipeline on a queue nobody drains.
    m_reader->SetStreamSelection(MF_SOURCE_READER_ALL_STREAMS, FALSE);
    if (FAILED(m_reader->SetStreamSelection(MF_SOURCE_READER_FIRST_VIDEO_STREAM, TRUE)))
    {
        Close();
        return false;
    }

    ComPtr<IMFMediaType> outputType;
    if (FAILED(MFCreateMediaType(&outputType)))
    {
        Close();
        return false;
    }
    outputType->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    outputType->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
    if (FAILED(m_reader->SetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, outputType.Get())))
    {
        Close();
        return false;
    }

    // Read back what we actually got rather than trusting what we asked for.
    ComPtr<IMFMediaType> actualType;
    if (FAILED(m_reader->GetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, &actualType)))
    {
        Close();
        return false;
    }
    UINT32 width = 0, height = 0;
    if (FAILED(MFGetAttributeSize(actualType.Get(), MF_MT_FRAME_SIZE, &width, &height))
        || width == 0 || height == 0)
    {
        Close();
        return false;
    }
    m_width = width;
    m_height = height;

    // Orientation comes from MF_MT_DEFAULT_STRIDE and NOWHERE else. A
    // negative value there means the buffer holds the picture bottom-up;
    // anything else, including the attribute being absent, means top-down.
    //
    // The absent case is the one that matters and the one that was wrong
    // here at first. RGB32 is bottom-up *by convention* -- it is the old GDI
    // DIB layout -- so both MFGetStrideForBitmapInfoHeader and any
    // hand-rolled guess hand back a negative stride for it. But the video
    // processor that ENABLE_ADVANCED_VIDEO_PROCESSING inserts to get us here
    // emits top-down and simply doesn't set the attribute, so trusting that
    // convention as a fallback flipped every frame upside down. The
    // convention describes the format; only the attribute describes THIS
    // buffer, and no attribute is not a vote for bottom-up.
    INT32 declaredStride = 0;
    const bool haveStride =
        SUCCEEDED(actualType->GetUINT32(MF_MT_DEFAULT_STRIDE, reinterpret_cast<UINT32*>(&declaredStride)))
        && declaredStride != 0;
    m_bottomUp = haveStride && declaredStride < 0;
    m_stride = haveStride
        ? (declaredStride < 0 ? -declaredStride : declaredStride)
        : static_cast<INT32>(m_width * 4);

    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = m_width;
    desc.Height = m_height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    // MF's "RGB32" is BGRA in memory order, which is exactly this format --
    // no per-pixel swizzle needed, the upload is a straight row copy.
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_DYNAMIC;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    if (FAILED(device->CreateTexture2D(&desc, nullptr, &m_texture)))
    {
        Close();
        return false;
    }
    if (FAILED(device->CreateShaderResourceView(m_texture.Get(), nullptr, &m_srv)))
    {
        Close();
        return false;
    }

    m_finished = false;
    m_frameTime = -1.0;
    return true;
}

bool VideoPlayer::Update(ID3D11DeviceContext* context, float elapsedSeconds)
{
    if (!m_reader || !context || m_finished)
    {
        return false;
    }

    // Decode forward until the frame in hand is the one that belongs on
    // screen now. The loop (rather than one read per frame) is what makes a
    // hitch cost dropped frames instead of desync: if the app stalls for
    // 200ms, this catches up through the frames that expired during the
    // stall and shows the right one, at the price of decoding them.
    while (m_frameTime < static_cast<double>(elapsedSeconds))
    {
        DWORD streamFlags = 0;
        LONGLONG timestamp = 0;
        IMFSample* sample = nullptr;
        const HRESULT hr = m_reader->ReadSample(
            MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, nullptr, &streamFlags, &timestamp, &sample);
        if (FAILED(hr))
        {
            SafeRelease(sample);
            m_finished = true;
            return false;
        }
        if (streamFlags & MF_SOURCE_READERF_ENDOFSTREAM)
        {
            SafeRelease(sample);
            m_finished = true;
            return false;
        }
        if (!sample)
        {
            // A gap or a format change with no data attached -- not the end,
            // so keep asking.
            continue;
        }

        m_frameTime = static_cast<double>(timestamp) / kHnsPerSecond;

        IMFMediaBuffer* buffer = nullptr;
        if (SUCCEEDED(sample->ConvertToContiguousBuffer(&buffer)) && buffer)
        {
            BYTE* data = nullptr;
            DWORD maxLength = 0, currentLength = 0;
            if (SUCCEEDED(buffer->Lock(&data, &maxLength, &currentLength)) && data)
            {
                D3D11_MAPPED_SUBRESOURCE mapped = {};
                if (SUCCEEDED(context->Map(m_texture.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)))
                {
                    const size_t rowBytes = static_cast<size_t>(m_width) * 4;
                    const size_t absStride = static_cast<size_t>(m_stride);
                    for (uint32_t y = 0; y < m_height; ++y)
                    {
                        // Bottom-up means the buffer's first row is the LAST
                        // row of the picture, so walk it backwards into the
                        // texture. See the stride note in Open().
                        const uint32_t sourceRow = m_bottomUp ? (m_height - 1 - y) : y;
                        const BYTE* src = data + static_cast<size_t>(sourceRow) * absStride;
                        BYTE* dst = static_cast<BYTE*>(mapped.pData) + static_cast<size_t>(y) * mapped.RowPitch;
                        memcpy(dst, src, std::min(rowBytes, static_cast<size_t>(mapped.RowPitch)));
                    }
                    context->Unmap(m_texture.Get(), 0);
                }
                buffer->Unlock();
            }
        }
        SafeRelease(buffer);
        SafeRelease(sample);
    }

    return true;
}
