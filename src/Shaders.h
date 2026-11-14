#pragma once

// All shaders for the 2D quad renderer, as one HLSL source string compiled
// at runtime via D3DCompile (see QuadRenderer::Initialize) -- avoids an
// offline shader-compilation build step, at the (negligible, for a handful
// of tiny shaders) cost of a few milliseconds at startup.
//
// One vertex shader (screen-pixel-space quad -> clip space) feeds four
// pixel shader entry points, one per draw "kind": solid-fill rects, an
// RGBA image, single-channel (R8) text-atlas glyphs tinted by color, and
// an up-to-8-stop linear/radial gradient. Splitting by entry point (rather
// than a mode branch inside one PS) keeps each shader trivial and avoids
// binding textures/constant buffers a given draw doesn't need.
inline const char* kShaderSource = R"(
cbuffer FrameCB : register(b0)
{
    float2 ScreenSize;
    float2 _padFrame;
};

cbuffer DrawCB : register(b1)
{
    float4 Tint;
    // 0 = draw the full quad, 1 = mask to the ellipse inscribed in it (see
    // QuadRenderer::DrawRect's doc comment -- this is how XuiFigure circles
    // and rings, e.g. the connection-test progress indicators, get drawn as
    // actual circles instead of their flat bounding-box rect).
    float ShapeMode;
    float3 _padDraw;
};

#define MAX_GRADIENT_STOPS 8

cbuffer GradientCB : register(b2)
{
    float4 StopColor[MAX_GRADIENT_STOPS];
    float4 StopPos[MAX_GRADIENT_STOPS]; // .x used, .yzw padding
    uint GradientMode;   // 0 = linear (along Axis), 1 = radial (from quad center)
    uint StopCount;
    float2 Axis;         // linear gradient direction, from the fill's Rotation (see QuadRenderer::DrawGradientRect)
};

Texture2D Tex : register(t0);
SamplerState Samp : register(s0);

struct VSInput
{
    float2 pos : POSITION;
    float2 uv  : TEXCOORD0;
};

struct PSInput
{
    float4 pos : SV_POSITION;
    float2 uv  : TEXCOORD0;
};

PSInput VSMain(VSInput input)
{
    PSInput output;
    // Pixel-space (origin top-left, +y down, matching the manifest's
    // coordinate convention) -> D3D clip space (origin center, +y up).
    float2 ndc = float2(
        (input.pos.x / ScreenSize.x) * 2.0 - 1.0,
        1.0 - (input.pos.y / ScreenSize.y) * 2.0);
    output.pos = float4(ndc, 0.0, 1.0);
    output.uv = input.uv;
    return output;
}

// Returns 1 inside the ellipse inscribed in the quad, 0 outside it, with a
// ~1-pixel antialiased edge (via screen-space derivatives, so it stays a
// crisp single-pixel ring regardless of how large the quad is drawn).
// ShapeMode is a per-draw float (not a bool) because HLSL cbuffer packing
// makes a plain bool awkward across the C++/HLSL boundary; treated as a
// bool via the >= 0.5 comparison below.
float EllipseCoverage(float2 uv)
{
    float2 c = (uv - 0.5) * 2.0; // -1..1 across the quad, unit circle inscribed
    float dist = length(c);
    float aa = max(fwidth(dist), 0.0001);
    return 1.0 - smoothstep(1.0 - aa, 1.0 + aa, dist);
}

float4 PSSolid(PSInput input) : SV_TARGET
{
    float4 color = Tint;
    if (ShapeMode >= 0.5)
    {
        color.a *= EllipseCoverage(input.uv);
    }
    return color;
}

float4 PSImage(PSInput input) : SV_TARGET
{
    float4 color = Tex.Sample(Samp, input.uv) * Tint;
    if (ShapeMode >= 0.5)
    {
        color.a *= EllipseCoverage(input.uv);
    }
    return color;
}

float4 PSText(PSInput input) : SV_TARGET
{
    float a = Tex.Sample(Samp, input.uv).r;
    return float4(Tint.rgb, Tint.a * a);
}

float4 PSGradient(PSInput input) : SV_TARGET
{
    float t;
    if (GradientMode == 1)
    {
        // Radial: 0 at quad center, 1 at the inscribed-circle edge.
        t = saturate(length(input.uv - float2(0.5, 0.5)) * 2.0);
    }
    else
    {
        // Linear along the authored axis. XUI's un-rotated gradient runs
        // LEFT TO RIGHT; Axis carries the fill's own Rotation applied to
        // that, so an Axis of (0,1) reproduces the old top-to-bottom
        // behaviour and (0,-1) -- the button focus highlight -- is the same
        // ramp reversed, which hardcoding uv.y got backwards.
        t = saturate(dot(input.uv - 0.5, Axis) + 0.5);
    }

    float4 result;
    if (StopCount == 0)
    {
        result = float4(1, 0, 1, 1); // shouldn't happen -- obvious-magenta signals a cook/bind bug rather than blending wrong
    }
    else if (t <= StopPos[0].x)
    {
        result = StopColor[0];
    }
    else
    {
        result = StopColor[StopCount - 1];
        for (uint i = 0; i < StopCount - 1; ++i)
        {
            float p0 = StopPos[i].x;
            float p1 = StopPos[i + 1].x;
            if (t >= p0 && t <= p1)
            {
                float span = max(p1 - p0, 0.0001);
                float localT = (t - p0) / span;
                result = lerp(StopColor[i], StopColor[i + 1], localT);
                break;
            }
        }
    }

    // Without this, a radial fill's corners (outside the inscribed circle,
    // where the loop above never finds a bracketing [p0,p1] pair) fall
    // through to StopColor[StopCount-1] -- i.e. get PAINTED with whatever
    // color sits at the outer edge, rather than excluded. That's invisible
    // when the outer stop happens to be fully transparent (many of the
    // authored ring/dot fills are), but wrong in general, and it's what
    // made every radial "circle" actually render as a flat-shaded square
    // whenever the outer stop wasn't transparent. ShapeMode fixes it for
    // real by discarding those corner pixels outright instead of relying
    // on a stop's alpha to hide them.
    // Gradients are the only draw kind that builds its own colour rather
    // than taking it from Tint, so without this a caller has no way to fade
    // one -- which is what a scene cross-fade needs to do to EVERY draw.
    // A no-op for ordinary gradient draws: DrawGradientRect passes opaque
    // white, and white*x == x.
    result *= Tint;

    if (ShapeMode >= 0.5)
    {
        result.a *= EllipseCoverage(input.uv);
    }
    return result;
}
)";
