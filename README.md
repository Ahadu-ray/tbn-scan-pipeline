# TBN food scan pipeline

Batch pipeline for the TBN AR menu: three phones film a dish on a turntable, and this
turns the videos into a web-ready GLB, stopping at the steps you do by hand.

```
videos ─▶ extract ─▶ select ─▶ mask ─▶ reconstruct ─▶ blender ─▶ [cleanup ▸ retopo ▸ bake] ─▶ export
          ffmpeg    sharpness   BiRefNet   RealityScan     orient,       you, in Blender      gltf-transform
                    filter      (rembg)    (or Meshroom)   scale, proxy  + Substance          meshopt/Draco + KTX2
```

## Install (once per workstation)

1. Python 3.10+ and `pip install -r requirements.txt` (`rembg[gpu]` instead of `rembg[cpu]` if you have an NVIDIA GPU).
2. ffmpeg on PATH. Blender 4.2+ (tested with Blender 5.3's Python module). RealityScan 2.x (Windows) or Meshroom.
3. Node.js 18+ for `gltf-transform` (fetched by `npx` on first use) and
   [KTX-Software](https://github.com/KhronosGroup/KTX-Software/releases) on PATH for KTX2 textures.
4. Put the tool paths in `<Restaurant>/restaurant.yaml` if they are not on PATH.

The first masking run downloads the BiRefNet model (about 1 GB).

## Folder layout

```
ABC Restaurant/
  restaurant.yaml                 tool paths + restaurant defaults (e.g. usual plate size)
  Shared_Assets/{Plates,Materials,Injera}
  Dishes/<dish>/
    dish.yaml                     plate size, profile, flip/orient overrides
    pipeline_state.json           which stages are done (written by the tool)
    Photos/video/low.mp4 mid.mp4 top.mp4   ◀ you drop these in (optional 2nd pass: low_2.mp4 …)
    Photos/frames/<ring>/         all extracted frames
    Photos/selected/              sharp frames + <frame>.jpg.mask.png  (reconstruction input)
    Scan/                         <dish>_raw.obj + texture, <dish>.rsproj, logs
    Highpoly/<dish>_high.blend    oriented, real-size, origin at plate bottom (bake source)
    Lowpoly/<dish>_proxy.blend    decimated proxy to start retopo from
    Lowpoly/<dish>_low.blend      ◀ your finished low-poly (or <dish>_low.glb)
    Lowpoly/textures/             ◀ Substance exports (BaseColor / Normal / OcclusionRoughnessMetallic)
    Export/<dish>.glb             final compressed web asset
```

## Daily use

```bash
python -m tbnscan init   "D:/Scans/ABC Restaurant" --dishes tibs shiro doro_wat
python -m tbnscan add    "D:/Scans/ABC Restaurant" kitfo --plate-cm 24 --profile detailed

# copy each dish's three videos into Dishes/<dish>/Photos/video/, then:
python -m tbnscan run    "D:/Scans/ABC Restaurant"            # all dishes, up to the first manual step
python -m tbnscan status "D:/Scans/ABC Restaurant"

# after you clean up, retopo and bake a dish:
python -m tbnscan mark   "D:/Scans/ABC Restaurant" tibs cleanup retopo bake
python -m tbnscan run    "D:/Scans/ABC Restaurant" --dish tibs   # runs export
```

`run` skips stages already done, so you can run it overnight for the whole restaurant and
again later. Useful flags: `--dish a b`, `--from mask --to blender`, `--force` (redo), `-v` (debug log).
A failed stage is recorded in `pipeline_state.json` with the error, and the other dishes keep going.

## What each stage does

- **extract**: pulls `extract.fps` frames per second from each ring video (3 fps ≈ 120–180 frames for a
  40–60 s turn), trimming the first/last second. Record SDR, not HDR, on the phones.
- **select**: scores sharpness (variance of the Laplacian on the centre of the frame), splits the turn into
  equal time slices and keeps the sharpest frame from each, so coverage stays even. Default 50 frames per
  ring (70 for `detailed`). Warns if a stretch of the turn has no sharp frames. See `Photos/selection_report.json`.
- **mask**: BiRefNet (through rembg) cuts out dish + plate, keeps the largest blob, fills holes (dark
  sauces), grows the edge a few pixels. Masks are written as `<frame>.jpg.mask.png`, the name RealityScan
  picks up as a mask layer. With a turntable this step is essential: the backdrop does not move, so
  unmasked frames confuse alignment.
- **reconstruct**: RealityScan headless: new scene, add frames, align, largest component, auto region,
  normal (or `quality: high`) model, texture, save project, export `Scan/<dish>_raw.obj`.
  With `engine: meshroom` the frames are given to Meshroom with the background blacked out.
- **blender**: imports the scan, finds the plate plane (PCA) and turns it flat with the food up, scales so
  the plate's longest side equals `plate_diameter_cm`, puts the origin at the plate bottom centre (Z = 0),
  merges doubles, caps the face count, saves `Highpoly/<dish>_high.blend`, then saves a decimated
  `Lowpoly/<dish>_proxy.blend`. Report in `Highpoly/blender_report.json`.
- **cleanup / retopo / bake**: yours. Mark them done with `mark`.
- **export**: takes `Lowpoly/<dish>_low.glb`, or exports `Lowpoly/<dish>_low.blend` through Blender
  (wiring up `Lowpoly/textures/` if the mesh has no textured material), then runs
  `gltf-transform optimize` (meshopt by default, KTX2 textures, max 2048 px, no auto-simplify).
  Warns above 5 MB.

## Hooks

`hooks:` in any yaml runs a shell command after reconstruct / blender / export, e.g. to open the
result or sync to cloud storage:

```yaml
hooks:
  after_export: 'rclone copy "{dish_dir}/Export" remote:tbn/{dish}'
```

## Things to check on the first real dish

- **Orientation**: auto-orient assumes the plate is the widest, flattest part. For glasses/bottles set
  `blender.auto_orient: false`; if a dish comes out upside down, set `blender.flip: true` and rerun
  `--from blender --force`.
- **Plate size**: measure the real plate. Everything is scaled from it, and AR shows true size.
- **RealityScan masks**: confirm in the RealityScan GUI (open `Scan/<dish>.rsproj`) that the images show a
  mask layer. If your version expects another name, change `mask.mask_name`.
- **Texture size**: RealityScan's default texture settings apply; add `-set` options in
  `reconstruct.extra_args` to change them.

## Tested so far

Tested here on Linux: folder setup, extract + select on synthetic 3-ring video (blur detection and gap
warning work), mask clean-up logic, plate orientation maths, the Blender step and GLB export (with the
`bpy` 5.3 module) on a synthetic plate, and `gltf-transform` meshopt compression.
Not tested here: rembg/BiRefNet inference, RealityScan and Meshroom (need your Windows machine / GPU), KTX2 encoding.
