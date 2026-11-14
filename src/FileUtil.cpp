#include "FileUtil.h"

#include <windows.h>

std::vector<uint8_t> FileUtil::ReadWholeFileBinary(const std::wstring& path)
{
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
        OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE)
    {
        return {};
    }

    LARGE_INTEGER size = {};
    if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0)
    {
        CloseHandle(file);
        return {};
    }

    std::vector<uint8_t> buf(static_cast<size_t>(size.QuadPart));
    DWORD bytesRead = 0;
    BOOL ok = ReadFile(file, buf.data(), static_cast<DWORD>(buf.size()), &bytesRead, nullptr);
    CloseHandle(file);
    if (!ok || bytesRead != buf.size())
    {
        return {};
    }
    return buf;
}

bool FileUtil::ReadWholeFileText(const std::wstring& path, std::string& outText)
{
    std::vector<uint8_t> bytes = ReadWholeFileBinary(path);
    if (bytes.empty())
    {
        return false;
    }
    outText.assign(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    return true;
}

std::wstring FileUtil::GetExecutableDir()
{
    wchar_t buf[MAX_PATH] = {};
    DWORD len = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    if (len == 0 || len == MAX_PATH)
    {
        return L".\\"; // fall back to CWD rather than an empty/garbage path
    }
    std::wstring path(buf, len);
    size_t lastSlash = path.find_last_of(L"\\/");
    if (lastSlash == std::wstring::npos)
    {
        return L".\\";
    }
    return path.substr(0, lastSlash + 1);
}

std::wstring FileUtil::Utf8ToWide(const std::string& s)
{
    if (s.empty())
    {
        return {};
    }
    int needed = MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0);
    if (needed <= 0)
    {
        return {};
    }
    std::wstring result(static_cast<size_t>(needed), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), result.data(), needed);
    return result;
}

std::string FileUtil::WideToUtf8(const std::wstring& s)
{
    if (s.empty())
    {
        return std::string();
    }
    int needed = WideCharToMultiByte(CP_UTF8, 0, s.c_str(), static_cast<int>(s.size()), nullptr, 0, nullptr, nullptr);
    if (needed <= 0)
    {
        return std::string();
    }
    std::string out(static_cast<size_t>(needed), '\0');
    WideCharToMultiByte(CP_UTF8, 0, s.c_str(), static_cast<int>(s.size()), out.data(), needed, nullptr, nullptr);
    return out;
}
