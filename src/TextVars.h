#pragma once

#include <map>
#include <string>

// Text that scenes show by name. A TEXT whose whole string is "{name}" draws
// what is held under `name` here (Scene.cpp), a manifest's "SAY name text"
// gives a name its first value, and the on-screen keyboard's keys type into
// "typed" (Navigator::Activate). One table for the whole app, because the
// keyboard is a scene of the guide and the field it fills is the dashboard's.
// It lasts until the app closes.
inline std::map<std::string, std::string>& TextVars()
{
    static std::map<std::string, std::string> vars;
    return vars;
}

// Sets the keyboard up for `field`, before it is shown: it opens on what the
// field holds -- nothing, while that is still its "<field>.unset" wording --
// under the field's own title and prompt ("SAY <field>.title ..."). Its Done
// key writes the field (Navigator's KEY:DONE).
inline void BeginTyping(const std::string& field)
{
    std::map<std::string, std::string>& text = TextVars();
    const std::string unset = text[field + ".unset"];
    text["field"] = field;
    text["typed"] = (!unset.empty() && text[field] == unset) ? std::string() : text[field];
    text["kbtitle"] = text[field + ".title"];
    text["kbprompt"] = text[field + ".prompt"];
}
