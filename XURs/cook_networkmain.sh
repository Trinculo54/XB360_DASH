#!/bin/sh
# Cooks NetworkMain the way the console actually composites it. Every flag
# below is a call the layout file cannot make on its own -- see the README's
# "Matching the console" section for what each one is derived from.
SEARCH="--search-dir ../DASH/dash_bin_embedded_assets --search-dir ../XZP"
OUT=../assets/scenes/NetworkMain

RINGS=""
for i in 1 2 3 4 5 6 7 8 9 10; do RINGS="$RINGS --hide-id HalfRing$i"; done

python3 xur_cook.py "../DASH/dash_bin_embedded_assets/archive_18/NetworkMain.xur" \
  "$OUT/manifest.txt" --assets-dir "$OUT/assets" $SEARCH \
  --canvas 1280x720 --scene-pos 200,114 --backdrop \
  --hide-id txtPacketLossStatus --hide-id txtUploadSpeedStatus \
  --hide-id txtDownloadSpeedStatus --hide-id txtSignalStrengthStatus \
  --hide-id txtLinkSpeedStatus \
  --hide-id LiveStatusScene \
  --hide-id legend_a --hide-id legend_b \
  --hide-id Spinner --hide-id Spinner1 --hide-id Spinner2 \
  $RINGS \
  --show-id ProgressBar3_On \
  --place-id txt_Header=96,56 \
  --legend A=Select@96 --legend B=Back@196.4 \
  --item navConfigureSettings=GOTO:2004_NetworkDetails --item navTestLive=GOTO:TestingScene_Live \
  --item navTestPC=GOTO:TestingScene_PC --item navTestMcx=GOTO:TestingScene_MCX \
  --item navTestStatistics=GOTO:NetworkStatistics \
  "$@"

# The pages those rows open, and -- onto the manifest just written -- the
# values Network Settings shows once a test has run.
python3 network_pages.py $SEARCH --out-dir ../assets/scenes
