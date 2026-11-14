#include "SceneManifest.h"
#include "FileUtil.h"
#include "TextVars.h"

#include <sstream>
#include <cctype>

namespace
{
    std::string Trim(const std::string& s)
    {
        size_t begin = s.find_first_not_of(" \t\r\n");
        if (begin == std::string::npos)
        {
            return "";
        }
        size_t end = s.find_last_not_of(" \t\r\n");
        return s.substr(begin, end - begin + 1);
    }

    std::vector<std::string> SplitLines(const std::string& text)
    {
        std::vector<std::string> lines;
        std::string current;
        for (char c : text)
        {
            if (c == '\n')
            {
                lines.push_back(current);
                current.clear();
            }
            else if (c != '\r')
            {
                current.push_back(c);
            }
        }
        if (!current.empty())
        {
            lines.push_back(current);
        }
        return lines;
    }

    // Reads the free-text tail of a TEXT line (the string after its fixed
    // fields) -- istringstream's operator>> stops at whitespace, so token
    // extraction can't be used for the text itself (it may contain spaces).
    // `iss` must already have consumed exactly the fixed fields.
    std::string ReadRemainder(std::istringstream& iss)
    {
        std::string rest;
        std::getline(iss, rest);
        // getline leaves the single separating space (between the last
        // numeric field and the text) at the front -- drop just that one.
        if (!rest.empty() && rest.front() == ' ')
        {
            rest.erase(0, 1);
        }
        return rest;
    }
}

float SceneManifest::ApplyEase(Ease ease, float p)
{
    if (p <= 0.0f) return 0.0f;
    if (p >= 1.0f) return 1.0f;
    switch (ease)
    {
    case Ease::In:    return p * p;                     // slow off the mark, fastest at the end
    case Ease::Out:   return 1.0f - (1.0f - p) * (1.0f - p); // quickest first, settles in
    case Ease::InOut: return p * p * (3.0f - 2.0f * p); // smoothstep: eased at both ends
    case Ease::Linear:
    default:          return p;
    }
}

bool SceneManifest::LoadFromFile(const std::wstring& path)
{
    std::string text;
    if (!FileUtil::ReadWholeFileText(path, text))
    {
        return false;
    }

    std::vector<std::string> lines = SplitLines(text);
    m_commands.clear();
    m_items.clear();
    m_defaultFocus.clear();
    m_focusBlendSeconds = 0.0f;
    m_pressInSeconds = 0.0f;
    m_pressOutSeconds = 0.0f;
    m_introSeconds = 0.0f;
    m_introRiseY = 0.0f;
    m_introHoldSeconds = 0.0f;
    m_introEase = Ease::Linear;
    m_canvasWidth = 0.0f;
    m_canvasHeight = 0.0f;
    m_tracks.clear();
    m_marks.clear();
    m_after.clear();
    m_sets.clear();
    m_enters.clear();
    m_vars.clear();
    int currentTrack = -1;   // the TRACK block being read, -1 outside one
    int ifVar = -1;          // the IF / WHEN in force, -1 for none
    int ifValue = 0;

    // Draw commands go to the scene's static list until an ITEM opens, then
    // to that item's normal/focus list until ENDITEM closes it. Keeping this
    // as a redirected output pointer (rather than a parse mode enum) means
    // every existing command parser below stays untouched.
    std::vector<ManifestCommand>* sink = &m_commands;

    // Sticky across commands until the next LAYER line, so a run of chrome
    // doesn't have to repeat itself on every line.
    bool layerFixed = false;
    bool layerList = false;
    float pulseSeconds = 0.0f;
    float pulseFloor = 0.0f;
    std::string fontFile;   // current FONT directive; empty = the scene's default
    bool explicitDefaultFocus = false;  // a DEFAULTFOCUS line beats declaration order

    // The camera position at which following commands are fully opaque.
    // Inside an ITEM this defaults to that item's own viewX -- a tile belongs
    // to the page it is focused from -- so only free-standing page content
    // (the black panels) needs to say so explicitly.
    float fadeAnchor = 0.0f;
    float topLevelFadeAnchor = 0.0f;

    for (size_t i = 0; i < lines.size(); ++i)
    {
        std::string line = Trim(lines[i]);
        if (line.empty() || line[0] == '#')
        {
            continue;
        }

        std::istringstream iss(line);
        std::string cmd;
        iss >> cmd;

        if (cmd == "INTRO")
        {
            iss >> m_introSeconds >> m_introRiseY >> m_introHoldSeconds;
            if (m_introSeconds < 0.0f) m_introSeconds = 0.0f;
            if (m_introHoldSeconds < 0.0f) m_introHoldSeconds = 0.0f;
            std::string ease;
            if (iss >> ease)
            {
                // Anything unrecognised stays Linear, which is what the
                // console's keyframes actually say -- an unknown name should
                // fall back to the source material, not to a guess.
                if (ease == "in") m_introEase = Ease::In;
                else if (ease == "out") m_introEase = Ease::Out;
                else if (ease == "inout") m_introEase = Ease::InOut;
                else m_introEase = Ease::Linear;
            }
            continue;
        }
        if (cmd == "PRESS")
        {
            iss >> m_pressInSeconds >> m_pressOutSeconds;
            if (m_pressInSeconds < 0.0f) m_pressInSeconds = 0.0f;
            if (m_pressOutSeconds < 0.0f) m_pressOutSeconds = 0.0f;
            continue;
        }
        if (cmd == "FOCUSBLEND")
        {
            iss >> m_focusBlendSeconds;
            if (m_focusBlendSeconds < 0.0f)
            {
                m_focusBlendSeconds = 0.0f;
            }
            continue;
        }
        if (cmd == "FADE")
        {
            iss >> m_offPageAlpha >> m_fadeSpan;
            if (m_fadeSpan <= 0.0f)
            {
                m_fadeSpan = 1.0f; // a zero span would make every fade a divide by zero
            }
            continue;
        }
        if (cmd == "FADEGROUP")
        {
            iss >> fadeAnchor;
            if (m_items.empty() || sink == &m_commands)
            {
                topLevelFadeAnchor = fadeAnchor;
            }
            continue;
        }
        if (cmd == "LAYER")
        {
            std::string which;
            iss >> which;
            layerList = (which == "list");
            layerFixed = (which == "fixed") || layerList;
            continue;
        }
        if (cmd == "LIST")
        {
            iss >> m_listX >> m_listY >> m_listW >> m_listH;
            continue;
        }
        if (cmd == "ROW")
        {
            if (!m_items.empty())
            {
                iss >> m_items.back().rowY >> m_items.back().rowH;
            }
            continue;
        }
        if (cmd == "DEFAULTFOCUS")
        {
            std::string which;
            iss >> which;
            if (!which.empty() && which != "-")
            {
                m_defaultFocus = which;
                explicitDefaultFocus = true;
            }
            continue;
        }
        if (cmd == "FONT")
        {
            // Sticky, like LAYER: applies to every TEXT that follows until
            // it's changed again. The dashboard sets different parts of one
            // screen in different faces -- the page selector is Segoe Xbox
            // Light where the tile labels are Xbox TC -- so the face has to
            // be per-run, and threading it through as a mode keeps the TEXT
            // line itself unchanged.
            std::string which;
            iss >> which;
            fontFile = (which == "-" || which == "default") ? std::string() : which;
            continue;
        }
        if (cmd == "ITEM")
        {
            ManifestItem item;
            iss >> item.id >> item.navUp >> item.navDown >> item.navLeft >> item.navRight >> item.action;
            // viewX is optional: a scene with no camera movement (every
            // settings screen) just omits it and gets 0.
            if (!(iss >> item.viewX))
            {
                item.viewX = 0.0f;
            }
            // "-" is the format's "no link" placeholder -- normalise it away
            // here so consumers can just test for empty.
            auto clear = [](std::string& v) { if (v == "-") { v.clear(); } };
            clear(item.navUp); clear(item.navDown); clear(item.navLeft); clear(item.navRight);
            clear(item.action);
            fadeAnchor = item.viewX;
            m_items.push_back(std::move(item));
            sink = &m_items.back().normal;
            if (m_defaultFocus.empty() && !explicitDefaultFocus)
            {
                // Fallback only: the first item declared. Fine for a cooked
                // .xur screen, where declaration order IS reading order, but
                // it silently couples "where focus starts" to "what the cook
                // script happens to emit first" -- which broke the hub the
                // moment the promo panel was emitted ahead of the tiles.
                // DEFAULTFOCUS says it outright instead.
                m_defaultFocus = m_items.back().id;
            }
            continue;
        }
        if (cmd == "PULSE")
        {
            // Sticky, like LAYER and FONT: applies to everything that
            // follows until it's turned off with "PULSE -" or 0.
            std::string first;
            iss >> first;
            if (first == "-" || first == "off")
            {
                pulseSeconds = 0.0f;
                pulseFloor = 0.0f;
            }
            else
            {
                pulseSeconds = static_cast<float>(atof(first.c_str()));
                if (!(iss >> pulseFloor))
                {
                    pulseFloor = 0.0f;
                }
            }
            continue;
        }
        if (cmd == "DISABLED")
        {
            // No operand, like RETURN, and likewise part of the ITEM block.
            if (!m_items.empty())
            {
                m_items.back().disabled = true;
            }
            continue;
        }
        if (cmd == "RETURN")
        {
            // No operand: an item either is a pass-through or isn't. Like
            // SHOULDER, it belongs to the ITEM block being built.
            if (!m_items.empty())
            {
                m_items.back().returnPoint = true;
            }
            continue;
        }
        if (cmd == "SHOULDER")
        {
            // Belongs to the ITEM being built, so it's a directive inside the
            // block rather than two more fields on the ITEM line -- which
            // would have broken every manifest already cooked.
            if (!m_items.empty())
            {
                std::string left, right;
                iss >> left >> right;
                m_items.back().pageLeft = (left == "-") ? std::string() : left;
                m_items.back().pageRight = (right == "-") ? std::string() : right;
            }
            continue;
        }
        if (cmd == "STATE")
        {
            std::string which;
            iss >> which;
            ifVar = -1;
            if (!m_items.empty())
            {
                sink = (which == "focus") ? &m_items.back().focus
                     : (which == "press") ? &m_items.back().press
                     : &m_items.back().normal;
            }
            continue;
        }
        if (cmd == "ENDITEM")
        {
            sink = &m_commands;
            fadeAnchor = topLevelFadeAnchor;
            ifVar = -1;
            continue;
        }
        if (cmd == "IF")
        {
            std::string name;
            iss >> name >> ifValue;
            ifVar = VarIndex(name);
            continue;
        }
        if (cmd == "ENDIF")
        {
            ifVar = -1;
            continue;
        }
        if (cmd == "VALUES")
        {
            if (!m_items.empty())
            {
                iss >> m_items.back().valueCount >> m_items.back().initialValue;
            }
            continue;
        }
        if (cmd == "WHEN")
        {
            // Until the next WHEN or STATE: these commands are this item at
            // one of its values.
            if (!m_items.empty())
            {
                iss >> ifValue;
                ifVar = VarIndex(m_items.back().id);
            }
            continue;
        }
        if (cmd == "TRACK")
        {
            ManifestTrack track;
            iss >> track.id >> track.x >> track.y >> track.w >> track.h;
            m_tracks.push_back(std::move(track));
            currentTrack = static_cast<int>(m_tracks.size()) - 1;
            continue;
        }
        if (cmd == "KEY")
        {
            if (currentTrack >= 0)
            {
                ManifestTrackKey key;
                std::string interp;
                int show = 1;
                iss >> key.frame >> interp >> key.easeIn >> key.easeOut >> key.easeScale >> show
                    >> key.dx >> key.dy >> key.sx >> key.sy >> key.alpha;
                key.interp = (interp == "linear") ? 1 : (interp == "ease") ? 2 : 0;
                key.show = (show != 0);
                m_tracks[currentTrack].keys.push_back(key);
            }
            continue;
        }
        if (cmd == "ENDTRACK")
        {
            currentTrack = -1;
            continue;
        }
        if (cmd == "MARK")
        {
            std::string name;
            float frame = 0.0f;
            iss >> name >> frame;
            m_marks.emplace_back(name, frame);
            continue;
        }
        if (cmd == "AFTER")
        {
            iss >> m_after;
            continue;
        }
        if (cmd == "SETS")
        {
            std::string name;
            int value = 0;
            iss >> name >> value;
            m_sets.emplace_back(name, value);
            continue;
        }
        if (cmd == "ACTIONIF")
        {
            if (!m_items.empty())
            {
                ManifestItem::Alternative alternative;
                iss >> alternative.var >> alternative.value >> alternative.action;
                m_items.back().alternatives.push_back(alternative);
            }
            continue;
        }
        if (cmd == "HIDEIF")
        {
            if (!m_items.empty())
            {
                std::pair<std::string, int> hidden;
                iss >> hidden.first >> hidden.second;
                m_items.back().hiddenIf.push_back(hidden);
            }
            continue;
        }
        if (cmd == "ENTER")
        {
            std::string name;
            int value = 0;
            iss >> name >> value;
            m_enters.emplace_back(name, value);
            continue;
        }
        if (cmd == "SAY")
        {
            // "SAY name text": a text variable's first value. Whatever has
            // been typed into it since is kept (emplace).
            std::string name, text;
            iss >> name;
            std::getline(iss >> std::ws, text);
            while (!text.empty() && (text.back() == '\r' || text.back() == ' '))
            {
                text.pop_back();
            }
            TextVars().emplace(name, text);
            continue;
        }

        if (cmd == "CANVAS")
        {
            iss >> m_canvasWidth >> m_canvasHeight;
        }
        else if (cmd == "RECT")
        {
            ManifestCommand c;
            c.type = ManifestCommandType::Rect;
            int shapeInt = 0;
            iss >> c.x >> c.y >> c.w >> c.h >> shapeInt >> c.color.a >> c.color.r >> c.color.g >> c.color.b;
            c.isEllipse = (shapeInt != 0);
            c.fixedToScreen = layerFixed;
            c.inList = layerList;
            c.fadeAnchorX = fadeAnchor;
            c.track = currentTrack;
            c.ifVar = ifVar;
            c.ifValue = ifValue;
            c.pulseSeconds = pulseSeconds;
            c.pulseFloor = pulseFloor;
            sink->push_back(std::move(c));
        }
        else if (cmd == "IMAGE")
        {
            ManifestCommand c;
            c.type = ManifestCommandType::Image;
            iss >> c.x >> c.y >> c.w >> c.h >> c.imageFile >> c.color.a >> c.color.r >> c.color.g >> c.color.b;
            c.fixedToScreen = layerFixed;
            c.inList = layerList;
            c.fadeAnchorX = fadeAnchor;
            c.track = currentTrack;
            c.ifVar = ifVar;
            c.ifValue = ifValue;
            c.pulseSeconds = pulseSeconds;
            c.pulseFloor = pulseFloor;
            sink->push_back(std::move(c));
        }
        else if (cmd == "TEXT" || cmd == "TEXTV")
        {
            ManifestCommand c;
            c.type = ManifestCommandType::Text;
            c.quarterTurn = (cmd == "TEXTV");
            iss >> c.x >> c.y >> c.w >> c.h >> c.hAlign >> c.vAlign >> c.pointSize
                >> c.color.a >> c.color.r >> c.color.g >> c.color.b;
            c.text = ReadRemainder(iss);
            c.fixedToScreen = layerFixed;
            c.inList = layerList;
            c.fadeAnchorX = fadeAnchor;
            c.track = currentTrack;
            c.ifVar = ifVar;
            c.ifValue = ifValue;
            c.pulseSeconds = pulseSeconds;
            c.pulseFloor = pulseFloor;
            c.fontFile = fontFile;
            sink->push_back(std::move(c));
        }
        else if (cmd == "GRADIENT_RECT")
        {
            ManifestCommand c;
            c.type = ManifestCommandType::GradientRect;
            int shapeInt = 0;
            int radialInt = 0;
            int stopCount = 0;
            iss >> c.x >> c.y >> c.w >> c.h >> shapeInt >> radialInt >> c.angleDegrees >> stopCount;
            c.isEllipse = (shapeInt != 0);
            c.radial = (radialInt != 0);

            for (int s = 0; s < stopCount && (i + 1) < lines.size(); ++s)
            {
                ++i;
                std::string stopLine = Trim(lines[i]);
                std::istringstream stopIss(stopLine);
                std::string stopCmd;
                stopIss >> stopCmd; // "STOP"
                ManifestGradientStop stop;
                stopIss >> stop.pos >> stop.color.a >> stop.color.r >> stop.color.g >> stop.color.b;
                c.stops.push_back(stop);
            }
            c.fixedToScreen = layerFixed;
            c.inList = layerList;
            c.fadeAnchorX = fadeAnchor;
            c.track = currentTrack;
            c.ifVar = ifVar;
            c.ifValue = ifValue;
            c.pulseSeconds = pulseSeconds;
            c.pulseFloor = pulseFloor;
            sink->push_back(std::move(c));
        }
        // Unknown command words are silently skipped -- forward-compatible
        // with a cook-script format addition without needing this loader
        // rebuilt in lockstep, and a stray unrecognized line just draws
        // nothing rather than crashing the app.
    }

    return true;
}

int SceneManifest::FindItem(const std::string& id) const
{
    for (size_t i = 0; i < m_items.size(); ++i)
    {
        if (m_items[i].id == id)
        {
            return static_cast<int>(i);
        }
    }
    return -1;
}

float SceneManifest::EaseTime(float t, float easeIn, float easeOut, float easeScale)
{
    if (t <= 0.0f) return 0.0f;
    if (t >= 1.0f) return 1.0f;
    // Speed, as a fraction of top speed, at the two ends of the span...
    const float a = (easeIn <= -100.0f) ? 0.0f : 1.0f + easeIn / 100.0f;
    const float b = (easeOut >= 100.0f) ? 0.0f : 1.0f - easeOut / 100.0f;
    // ...and where in the span top speed is reached. Speed ramps linearly
    // a -> 1 up to there and 1 -> b after it; progress is the area under
    // that, normalised so the whole span covers exactly 1.
    const float s = (easeScale <= 0.0f) ? 0.0f : (easeScale >= 100.0f) ? 1.0f : easeScale / 100.0f;
    const float total = s * (a + 1.0f) * 0.5f + (1.0f - s) * (b + 1.0f) * 0.5f;
    if (total <= 0.0f)
    {
        return t;
    }
    float covered;
    if (t <= s)
    {
        covered = a * t + (1.0f - a) * t * t / (2.0f * s);
    }
    else
    {
        const float rest = 1.0f - s;
        covered = s * (a + 1.0f) * 0.5f
            + b * (t - s) + (1.0f - b) * (rest * rest - (1.0f - t) * (1.0f - t)) / (2.0f * rest);
    }
    return covered / total;
}

TrackState SceneManifest::Evaluate(const ManifestTrack& track, float frame)
{
    TrackState state;
    state.x = track.x; state.y = track.y; state.w = track.w; state.h = track.h;
    if (track.keys.empty())
    {
        return state;
    }
    // The last key at or before `frame`; before the first key, the first.
    size_t at = 0;
    while (at + 1 < track.keys.size() && track.keys[at + 1].frame <= frame)
    {
        ++at;
    }
    const ManifestTrackKey& from = track.keys[at];
    float f = 0.0f;
    const ManifestTrackKey* to = &from;
    if (from.interp != 0 && at + 1 < track.keys.size() && frame > from.frame)
    {
        to = &track.keys[at + 1];
        const float t = (frame - from.frame) / (to->frame - from.frame);
        f = (from.interp == 2) ? EaseTime(t, from.easeIn, from.easeOut, from.easeScale) : t;
    }
    state.visible = from.show;   // Show steps; it is never tweened
    state.dx = from.dx + (to->dx - from.dx) * f;
    state.dy = from.dy + (to->dy - from.dy) * f;
    state.sx = from.sx + (to->sx - from.sx) * f;
    state.sy = from.sy + (to->sy - from.sy) * f;
    state.alpha = from.alpha + (to->alpha - from.alpha) * f;
    return state;
}

std::vector<TrackState> SceneManifest::EvaluateTracks(float frame) const
{
    std::vector<TrackState> states;
    states.reserve(m_tracks.size());
    for (const ManifestTrack& track : m_tracks)
    {
        states.push_back(Evaluate(track, frame));
    }
    return states;
}

bool SceneManifest::EvaluateTrack(const std::string& id, float frame, TrackState& out) const
{
    for (const ManifestTrack& track : m_tracks)
    {
        if (track.id == id)
        {
            out = Evaluate(track, frame);
            return true;
        }
    }
    out = TrackState();
    out.visible = false;
    return false;
}

int SceneManifest::VarIndex(const std::string& name)
{
    for (size_t i = 0; i < m_vars.size(); ++i)
    {
        if (m_vars[i] == name)
        {
            return static_cast<int>(i);
        }
    }
    m_vars.push_back(name);
    return static_cast<int>(m_vars.size()) - 1;
}

float SceneManifest::GetMark(const std::string& name, float fallback) const
{
    for (const std::pair<std::string, float>& mark : m_marks)
    {
        if (mark.first == name)
        {
            return mark.second;
        }
    }
    return fallback;
}

float SceneManifest::FadeFactor(const ManifestCommand& command, float scrollX) const
{
    if (command.fixedToScreen || m_offPageAlpha >= 1.0f)
    {
        return 1.0f;
    }
    float distance = command.fadeAnchorX - scrollX;
    if (distance < 0.0f)
    {
        distance = -distance;
    }
    float t = distance / m_fadeSpan;
    if (t > 1.0f)
    {
        t = 1.0f;
    }
    return 1.0f + (m_offPageAlpha - 1.0f) * t;
}
