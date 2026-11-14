static bool g_trace = false;
static int  g_indent = 0;
#define LOG(...) do { } while (0)
static std::map<uint32_t,std::string> g_names;
static const char* Name(uint32_t h){ auto it=g_names.find(h); return it==g_names.end()?"?":it->second.c_str(); }
static uint32_t NewHandle(const char* kind, const std::string& name, uint32_t extra = 64)
{ uint32_t h = GuestAlloc(extra); g_names[h] = name.empty()?kind:name; return h; }
// ---- resource creation ----------------------------------------------------
// Shader creation takes the guest address of the shader blob, which is the
// one thing that identifies which of the 17 it is.
static const struct { uint32_t addr; const char* name; } kShaders[] = {
    {0x98000690,"vs00_Basic"},      {0x980007D8,"ps01_Solid"},
    {0x980008E0,"vs02_Textured"},   {0x98000A08,"ps03_MatClr"},
    {0x98000AD0,"ps04_TwoTexUOfs"}, {0x98000D60,"vs05_ShadowProj"},
    {0x98001018,"ps06_Texture"},    {0x98001128,"ps07_TexMatClr"},
    {0x98001248,"ps08_GlowComposite"},{0x98001528,"vs09_LitWorld"},
    {0x98001740,"ps10_LitMaterial"},{0x98001C40,"ps11_BlurA"},
    {0x98001EF0,"ps12_BlurB"},      {0x98002338,"vs13_Overlay"},
    {0x98002418,"ps14_ClrAdd"},     {0x98002658,"vs15_Quad"},
    {0x980027A0,"ps16_Copy"},
};
static std::string ShaderName(uint32_t blob)
{
    for (auto& s : kShaders) if (s.addr == blob) return s.name;
    char b[64]; std::snprintf(b,sizeof b,"shader@%08X",blob); return b;
}

void sub_98028120(PPCContext& ctx, uint8_t* base) {           // CreateVertexShader
    (void)base; std::string n = ShaderName(ctx.r3.u32);
    uint32_t h = NewHandle("vs", n); LOG("CreateVertexShader  %s -> %08X", n.c_str(), h);
    ctx.r3.u64 = h;
}
void sub_98027F38(PPCContext& ctx, uint8_t* base) {           // CreatePixelShader
    (void)base; std::string n = ShaderName(ctx.r3.u32);
    uint32_t h = NewHandle("ps", n); LOG("CreatePixelShader   %s -> %08X", n.c_str(), h);
    ctx.r3.u64 = h;
}
static int g_texSeq = 0;
void sub_980268E0(PPCContext& ctx, uint8_t* base) {           // CreateTexture
    (void)base;
    uint32_t w = ctx.r3.u32, h = ctx.r4.u32;
    if (!w || w > 8192) w = 256;
    if (!h || h > 8192) h = 256;
    char nm[64]; std::snprintf(nm,sizeof nm,"tex%d_%ux%u", g_texSeq++, w, h);
    uint32_t obj = NewHandle("tex", nm, 128);
    uint32_t bits = GuestAlloc(w*h*4 + 4096);
    SU32(obj+0, bits); SU32(obj+4, w); SU32(obj+8, h);
    LOG("CreateTexture       %ux%u fmt=%08X -> %08X (%s)", w, h, ctx.r8.u32, obj, nm);
    ctx.r3.u64 = obj;
}
void sub_980268C0(PPCContext& ctx, uint8_t* base) {           // LockRect
    (void)base; uint32_t tex = ctx.r3.u32, out = ctx.r5.u32;
    SU32(out+0, GU32(tex+4)*4); SU32(out+4, GU32(tex+0));
}
static int g_surfSeq = 0;
void sub_98026A00(PPCContext& ctx, uint8_t* base) {           // CreateSurface
    (void)base; char nm[64]; std::snprintf(nm,sizeof nm,"surf%d_%ux%u", g_surfSeq++, ctx.r3.u32, ctx.r4.u32);
    uint32_t h = NewHandle("surf", nm, 128);
    LOG("CreateSurface       %ux%u -> %08X (%s)", ctx.r3.u32, ctx.r4.u32, h, nm);
    ctx.r3.u64 = h;
}
static int g_declSeq = 0;
void sub_98027C70(PPCContext& ctx, uint8_t* base) {           // CreateVertexDeclaration
    (void)base;
    // The declaration is an array of D3DVERTEXELEMENT9-alikes; dump it, it
    // is the only description of the vertex layout the app expects.
    char nm[32]; std::snprintf(nm,sizeof nm,"decl%d", g_declSeq++);
    uint32_t h = NewHandle("decl", nm, 128);
    if (g_trace) {
        std::printf("%*s  CreateVertexDecl    %s ->%08X  elements:", g_indent*2, "", nm, h);
        for (int i = 0; i < 8; ++i) {
            uint32_t p = ctx.r3.u32 + i*8;
            uint16_t stream = (uint16_t)(GU32(p) >> 16), offset = (uint16_t)(GU32(p) & 0xFFFF);
            uint32_t rest = GU32(p+4);
            if (stream == 0xFFFF) break;
            std::printf(" [s%u o%u %08X]", stream, offset, rest);
        }
        std::printf("\n");
    }
    ctx.r3.u64 = h;
}

// ---- state ----------------------------------------------------------------
void sub_98027950(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetVertexShader     %s", Name(ctx.r4.u32)); }
void sub_98027748(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetPixelShader      %s", Name(ctx.r4.u32)); }
void sub_98026B38(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetTexture          stage %u = %s", ctx.r4.u32, Name(ctx.r5.u32)); }
void sub_980235C0(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetSampler[%u].min  %u", ctx.r4.u32, ctx.r5.u32); }
void sub_98023768(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetSampler[%u].mag  %u", ctx.r4.u32, ctx.r5.u32); }
void sub_980240E0(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetRenderStateA     %u %u %u %u %u", ctx.r4.u32,ctx.r5.u32,ctx.r6.u32,ctx.r7.u32,ctx.r8.u32); }
void sub_98024200(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetRenderStateB     %u %u", ctx.r4.u32, ctx.r5.u32); }
void sub_980224B8(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetZFunc/Enable     %u", ctx.r4.u32); }
void sub_98022488(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetZWrite           %u", ctx.r4.u32); }
void sub_98021D20(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetCullMode         %u", ctx.r4.u32); }
void sub_9802C190(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetBlend            %u %u %u", ctx.r4.u32,ctx.r5.u32,ctx.r6.u32); }
void sub_980229E8(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetAlphaState       %u %u", ctx.r4.u32,ctx.r5.u32); }
void sub_98022448(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetState98022448    %u", ctx.r4.u32); }
void sub_980221E0(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetState980221E0    %u", ctx.r4.u32); }
void sub_98022240(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetState98022240    %u", ctx.r4.u32); }
void sub_98024948(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetState98024948    %u", ctx.r4.u32); }
void sub_98027638(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetPixelShaderConstF start=%u count=%u", ctx.r4.u32, ctx.r6.u32); }

// ---- targets / draws ------------------------------------------------------
void sub_98024290(PPCContext& ctx, uint8_t* base){ (void)base; LOG("GetRenderTarget     %u", ctx.r4.u32); ctx.r3.u64 = NewHandle("rt","backbufferRT"); }
void sub_980242D8(PPCContext& ctx, uint8_t* base){ (void)base; LOG("GetDepthStencil"); ctx.r3.u64 = NewHandle("ds","backbufferDS"); }
void sub_98024FB0(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetRenderTarget     %u = %s", ctx.r4.u32, Name(ctx.r5.u32)); }
void sub_98024CD8(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SetDepthStencil     %s", Name(ctx.r4.u32)); }
void sub_9802ACE8(PPCContext& ctx, uint8_t* base){ (void)base; LOG("Clear               flags=%u color=%08X", ctx.r6.u32, ctx.r7.u32); }
void sub_9802B038(PPCContext& ctx, uint8_t* base){ (void)base; LOG("ClearRects          count=%u flags=%u", ctx.r4.u32, ctx.r5.u32); }
void sub_9802B3A8(PPCContext& ctx, uint8_t* base){ (void)base; LOG("ResolveRect         -> %s", Name(ctx.r6.u32)); }
void sub_9802E190(PPCContext& ctx, uint8_t* base){ (void)base; LOG("Resolve             -> %s", Name(ctx.r6.u32)); }
void sub_9802E1E0(PPCContext& ctx, uint8_t* base){ (void)base; LOG("Resolve2"); }
void sub_9802BD30(PPCContext& ctx, uint8_t* base)
{
    (void)base;
    static const char* kPrim[] = {"?","POINTLIST","LINELIST","LINESTRIP","TRIANGLELIST",
                                  "TRIANGLESTRIP","TRIANGLEFAN","?","RECTLIST","?","?","?","?","QUADLIST"};
    uint32_t pt = ctx.r4.u32;
    LOG(">>> DrawPrimitiveUP  %s  prims=%u  stride=%u",
        pt < 14 ? kPrim[pt] : "?", ctx.r5.u32, ctx.r7.u32);
}
void sub_9802BD78(PPCContext& ctx, uint8_t* base){ (void)base; LOG(">>> Draw9802BD78 %u %u", ctx.r4.u32, ctx.r5.u32); }
void sub_98029958(PPCContext& ctx, uint8_t* base){ (void)base; LOG("Present"); }
void sub_980291E8(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SwapBegin"); }
void sub_980291F0(PPCContext& ctx, uint8_t* base){ (void)base; LOG("SwapTex %s", Name(ctx.r4.u32)); }
void sub_9802F3B8(PPCContext& ctx, uint8_t* base){ (void)base; (void)ctx; }   // Release
void sub_9802E738(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void sub_9802E800(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void sub_9802E810(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void sub_9802E8C0(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void sub_9802F918(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void sub_9802F968(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void sub_980250F0(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void sub_980268A8(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void sub_980268B8(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }

// Device creation, skipped: it would descend into the video kernel calls and
// nothing after it reads anything meaningful out of the device object.
void sub_9800F040(PPCContext& ctx, uint8_t* base)
{
    (void)base;
    SU32(0x98077954, FAKE_DEVICE);
    LOG("[CreateDevice skipped; g_pD3DDevice = %08X]", FAKE_DEVICE);
    ctx.r3.u64 = 0;
}
// Timing: fixed so a traced frame is reproducible.
void sub_98045A60(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void sub_98045A58(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void sub_98045A10(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void __imp__XeCryptRandom(PPCContext& ctx, uint8_t* base)
{
    (void)base; static uint32_t s = 0x12345678u;
    for (uint32_t i = 0; i < ctx.r4.u32; ++i) { s = s*1664525u+1013904223u; g_base[ctx.r3.u32+i] = (uint8_t)(s>>24); }
    ctx.r3.u64 = 0;
}
void __imp__KeSetEvent(PPCContext& ctx, uint8_t* base){ (void)base; ctx.r3.u64 = 0; }
void __imp__RtlEnterCriticalSection(PPCContext& ctx, uint8_t* base){ (void)ctx; (void)base; }
void __imp__RtlLeaveCriticalSection(PPCContext& ctx, uint8_t* base){ (void)ctx; (void)base; }
void __imp__RtlInitializeCriticalSection(PPCContext& ctx, uint8_t* base){ (void)ctx; (void)base; }











