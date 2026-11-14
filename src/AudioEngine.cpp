#include "AudioEngine.h"
#include "FileUtil.h"

// Trim the parts of miniaudio this app will never reach. The cues are all
// 16-bit PCM wavs pulled out of the console's wavebanks, so the mp3/flac
// decoders, the encoders and the waveform/noise generators are dead weight
// in both compile time and binary size. WAV decoding stays, obviously.
#define MA_NO_FLAC
#define MA_NO_MP3
#define MA_NO_ENCODING
#define MA_NO_GENERATION
#define MINIAUDIO_IMPLEMENTATION
#include "../third_party/miniaudio.h"

#include <array>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

namespace
{
    // How many cues can be sounding at once. Moving focus quickly overlaps
    // two or three clicks, and a select landing on top of a page slide is
    // normal, but nothing here layers deeply -- a voice that gets stolen at
    // 16 was going to be inaudible anyway.
    constexpr size_t kMaxVoices = 16;

    // Names as they appear in sounds.txt, indexed by SoundCue. Kept in this
    // order deliberately: the array is indexed by the enum, so a new cue must
    // be added to both or the static_assert below fails the build.
    const char* const kCueNames[] = {
        "first_start",
        "focus",
        "focus_inactive",
        "twist_focus",
        "twist_left",
        "twist_right",
        "select",
        "select_inactive",
        "back",
        "boot",
        "guide_open",
        "guide_close",
        "guide_blade",
        "guide_blade_open",
        "guide_tab_switch",
        "notify",
    };
    static_assert(sizeof(kCueNames) / sizeof(kCueNames[0]) == static_cast<size_t>(SoundCue::Count),
        "kCueNames must have exactly one entry per SoundCue");

    int CueIndexFromName(const std::string& name)
    {
        for (size_t i = 0; i < static_cast<size_t>(SoundCue::Count); ++i)
        {
            if (name == kCueNames[i])
            {
                return static_cast<int>(i);
            }
        }
        return -1;
    }
}

struct AudioEngineImpl
{
    // One loaded, fully decoded wav per cue. These are never started
    // themselves -- they are the thing Play() clones a voice from, which is
    // miniaudio's documented way of firing the same sound repeatedly without
    // re-reading or re-decoding it (ma_sound_init_copy shares the resource
    // manager's decoded buffer).
    struct Cue
    {
        ma_sound source;
        bool loaded = false;
        float gain = 1.0f;
    };

    struct Voice
    {
        ma_sound sound;
        bool active = false;
    };

    ma_engine engine;
    bool engineReady = false;
    std::array<Cue, static_cast<size_t>(SoundCue::Count)> cues;
    // Per-scene overrides, keyed "<scene>:<event>". Deliberately a separate
    // map rather than a second array per scene: overrides are rare (one
    // screen, a handful of events), and a miss here costs one failed lookup
    // before falling back to the default cue.
    std::unordered_map<std::string, Cue> sceneCues;
    // A plain array, not a vector: ma_sound is not relocatable (miniaudio
    // keeps internal pointers into it once started), so the storage has to
    // stay put for the lifetime of the voice.
    std::array<Voice, kMaxVoices> voices;
    std::string lastError;

    ~AudioEngineImpl()
    {
        for (Voice& voice : voices)
        {
            if (voice.active)
            {
                ma_sound_uninit(&voice.sound);
                voice.active = false;
            }
        }
        for (Cue& cue : cues)
        {
            if (cue.loaded)
            {
                ma_sound_uninit(&cue.source);
                cue.loaded = false;
            }
        }
        for (auto& entry : sceneCues)
        {
            if (entry.second.loaded)
            {
                ma_sound_uninit(&entry.second.source);
                entry.second.loaded = false;
            }
        }
        if (engineReady)
        {
            ma_engine_uninit(&engine);
            engineReady = false;
        }
    }
};

AudioEngine::AudioEngine() = default;
AudioEngine::~AudioEngine() = default;

bool AudioEngine::Initialize(const std::wstring& soundsDir)
{
    m_impl = std::make_unique<AudioEngineImpl>();

    const std::wstring listPath = soundsDir + L"\\sounds.txt";
    std::string listText;
    if (!FileUtil::ReadWholeFileText(listPath, listText) || listText.empty())
    {
        m_impl->lastError = "no sounds.txt in the sounds folder";
        return false;
    }

    if (ma_engine_init(nullptr, &m_impl->engine) != MA_SUCCESS)
    {
        // No output device, or one the OS won't hand us (headless session,
        // exclusive-mode hog). Not fatal -- the dashboard is still perfectly
        // usable in silence.
        m_impl->lastError = "couldn't open an audio output device";
        return false;
    }
    m_impl->engineReady = true;

    // Same flat, one-directive-per-line shape as the scene manifests:
    //   MASTER <gain>
    //   CUE <event> <file> <gain>
    // Unknown directives and unknown cue names are skipped rather than
    // rejected, so an entry for a cue this build doesn't have yet (or a
    // commented-out experiment) doesn't take the whole list down with it.
    std::istringstream stream(listText);
    std::string line;
    int loaded = 0;
    while (std::getline(stream, line))
    {
        if (!line.empty() && line.back() == '\r')
        {
            line.pop_back();
        }
        std::istringstream fields(line);
        std::string directive;
        if (!(fields >> directive) || directive.empty() || directive[0] == '#')
        {
            continue;
        }

        if (directive == "MASTER")
        {
            float gain = 1.0f;
            if (fields >> gain)
            {
                ma_engine_set_volume(&m_impl->engine, gain);
            }
            continue;
        }
        if (directive != "CUE")
        {
            continue;
        }

        std::string cueName, file;
        float gain = 1.0f;
        if (!(fields >> cueName >> file))
        {
            continue;
        }
        fields >> gain; // optional -- absent means unity

        // "<scene>:<event>" is an override for that one screen; a bare
        // "<event>" is the default used everywhere else.
        std::string scene;
        const size_t colon = cueName.find(':');
        if (colon != std::string::npos)
        {
            scene = cueName.substr(0, colon);
            cueName = cueName.substr(colon + 1);
        }

        const int index = CueIndexFromName(cueName);
        if (index < 0 || file == "-")
        {
            continue; // unknown cue, or one deliberately silenced with "-"
        }

        AudioEngineImpl::Cue& cue = scene.empty()
            ? m_impl->cues[static_cast<size_t>(index)]
            : m_impl->sceneCues[scene + ":" + cueName];
        if (cue.loaded)
        {
            continue; // first entry wins, so a duplicate line can't leak a sound
        }

        const std::wstring path = soundsDir + L"\\" + FileUtil::Utf8ToWide(file);
        // DECODE up front rather than streaming: these are all under two
        // seconds, and a UI click that has to touch the disk before it
        // sounds is a click that arrives after the thing it was reacting to.
        // NO_SPATIALIZATION/NO_PITCH keep it a straight stereo mix -- there
        // is no 3D scene here, and the pitch shifter would only cost cycles.
        const ma_uint32 flags = MA_SOUND_FLAG_DECODE
            | MA_SOUND_FLAG_NO_SPATIALIZATION
            | MA_SOUND_FLAG_NO_PITCH;
        if (ma_sound_init_from_file_w(&m_impl->engine, path.c_str(), flags, nullptr, nullptr,
                &cue.source) != MA_SUCCESS)
        {
            continue; // missing/corrupt wav -- that one cue stays silent
        }
        cue.loaded = true;
        cue.gain = gain;
        ++loaded;
    }

    if (loaded == 0)
    {
        m_impl->lastError = "sounds.txt named no wav this build could load";
        return false;
    }
    return true;
}

bool AudioEngine::IsLoaded() const
{
    return m_impl && m_impl->engineReady;
}

const std::string& AudioEngine::GetLastError() const
{
    static const std::string empty;
    return m_impl ? m_impl->lastError : empty;
}

void AudioEngine::Update()
{
    if (!m_impl || !m_impl->engineReady)
    {
        return;
    }
    // Reaping here rather than at Play time keeps the cost off the frame
    // where the input happened, and means a voice's resources come back
    // promptly even during a quiet stretch.
    for (AudioEngineImpl::Voice& voice : m_impl->voices)
    {
        if (voice.active && ma_sound_at_end(&voice.sound))
        {
            ma_sound_uninit(&voice.sound);
            voice.active = false;
        }
    }
}

void AudioEngine::StopAll()
{
    if (!m_impl || !m_impl->engineReady)
    {
        return;
    }
    for (AudioEngineImpl::Voice& voice : m_impl->voices)
    {
        if (voice.active)
        {
            ma_sound_uninit(&voice.sound);
            voice.active = false;
        }
    }
}

void AudioEngine::Play(SoundCue cue, const std::string& scene)
{
    if (!m_impl || !m_impl->engineReady || cue == SoundCue::Count)
    {
        return;
    }
    AudioEngineImpl::Cue* chosen = &m_impl->cues[static_cast<size_t>(cue)];
    if (!scene.empty())
    {
        auto it = m_impl->sceneCues.find(scene + ":" + kCueNames[static_cast<size_t>(cue)]);
        if (it != m_impl->sceneCues.end() && it->second.loaded)
        {
            chosen = &it->second;
        }
    }
    AudioEngineImpl::Cue& entry = *chosen;
    if (!entry.loaded)
    {
        return; // nothing configured for this event -- silence is a valid answer
    }

    AudioEngineImpl::Voice* slot = nullptr;
    for (AudioEngineImpl::Voice& voice : m_impl->voices)
    {
        if (!voice.active)
        {
            slot = &voice;
            break;
        }
        if (ma_sound_at_end(&voice.sound))
        {
            // Finished but not yet reaped by Update -- take it now rather
            // than dropping this cue for the sake of a bookkeeping lag.
            ma_sound_uninit(&voice.sound);
            voice.active = false;
            slot = &voice;
            break;
        }
    }
    if (!slot)
    {
        return; // all 16 genuinely sounding; adding a 17th would be mush
    }

    const ma_uint32 flags = MA_SOUND_FLAG_NO_SPATIALIZATION | MA_SOUND_FLAG_NO_PITCH;
    if (ma_sound_init_copy(&m_impl->engine, &entry.source, flags, nullptr, &slot->sound) != MA_SUCCESS)
    {
        return;
    }
    ma_sound_set_volume(&slot->sound, entry.gain);
    if (ma_sound_start(&slot->sound) != MA_SUCCESS)
    {
        ma_sound_uninit(&slot->sound);
        return;
    }
    slot->active = true;
}
