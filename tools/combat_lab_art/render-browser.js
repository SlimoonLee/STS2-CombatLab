/* This exporter uses the same Spine version as the game's skeletons. */
const canvas = document.getElementById('sprite');
const gl = canvas.getContext('webgl', {
  alpha: true,
  antialias: true,
  premultipliedAlpha: true,
  preserveDrawingBuffer: true,
});
if (!gl) throw new Error('WebGL is unavailable for asset rendering.');
const renderer = new spine.SceneRenderer(canvas, gl);

window.renderSprite = async function renderSprite(spec) {
  const assets = new spine.AssetManager(gl, '/source/');
  try {
    const parts = [...(spec.children || []), spec];
    for (const part of parts) {
      assets.loadTextureAtlas(part.atlas);
      assets.loadBinary(part.skeleton);
    }
    await assets.loadAll();
    if (assets.hasErrors()) throw new Error(JSON.stringify(assets.getErrors()));
    const poses = parts.map((part) => createPose(assets, part));
    const parent = poses.at(-1);
    // Godot mounts the carried egg as a separate SpineSprite on the item slot.
    for (let index = 0; index < parts.length - 1; index += 1) {
      const part = parts[index];
      const skeleton = poses[index].skeleton;
      const slot = parent.skeleton.findSlot(part.attachSlot);
      if (!slot) throw new Error(`Missing attach slot ${part.attachSlot} for ${spec.id}`);
      skeleton.x = part.position[0];
      skeleton.y = -part.position[1];
      skeleton.scaleX = part.scale[0];
      skeleton.scaleY = part.scale[1];
      skeleton.getRootBone().rotation -= part.rotation * 180 / Math.PI;
      skeleton.updateWorldTransformWith(spine.Physics.none, slot.bone);
    }
    const rectangles = poses.map(({ skeleton }) => {
      for (const slot of skeleton.slots) {
        if (slot.color.a * (slot.attachment?.color?.a ?? 1) < 0.005) slot.setAttachment(null);
      }
      return skeleton.getBoundsRect(new spine.SkeletonClipping());
    });
    const x = Math.min(...rectangles.map((rect) => rect.x));
    const y = Math.min(...rectangles.map((rect) => rect.y));
    const width = Math.max(...rectangles.map((rect) => rect.x + rect.width)) - x;
    const height = Math.max(...rectangles.map((rect) => rect.y + rect.height)) - y;
    if (!(width > 0 && height > 0)) throw new Error(`Empty sprite for ${spec.id}`);
    const extent = Math.max(width, height) / 0.92;
    renderer.camera.setViewport(extent, extent);
    renderer.camera.position.set(x + width / 2, y + height / 2, 0);
    renderer.camera.update();
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    renderer.begin();
    for (const pose of poses) renderer.drawSkeleton(pose.skeleton, pose.atlas.pages[0].pma);
    renderer.end();
    gl.finish();
    return {
      png: canvas.toDataURL('image/png'), animation: parent.tracks, skins: parent.selectedSkins,
      bounds: { x, y, width, height }, children: spec.children?.map((part) => part.id) || [],
    };
  } finally {
    assets.dispose();
  }
};

function createPose(assets, spec) {
    const atlas = assets.require(spec.atlas);
    const data = new spine.SkeletonBinary(new spine.AtlasAttachmentLoader(atlas))
      .readSkeletonData(assets.require(spec.skeleton));
    const skeleton = new spine.Skeleton(data);
    const selectedSkins = spec.skins || (data.findSkin(spec.previewSkin || '') ? [spec.previewSkin] : []);
    if (selectedSkins.length) {
      const skin = new spine.Skin('thumbnail');
      for (const name of selectedSkins) {
        const source = data.findSkin(name);
        if (!source) throw new Error(`Missing skin ${name} for ${spec.id}`);
        skin.addSkin(source);
      }
      skeleton.setSkin(skin);
    }
    skeleton.setToSetupPose();
    const animationState = new spine.AnimationState(new spine.AnimationStateData(data));
    const animationNames = data.animations.map((animation) => animation.name);
    const preferred = spec.animation || ['idle_loop', 'idle_loop1', 'awake_loop', 'sleep_loop', 'burrowed_loop', 'intro_loop']
      .find((name) => animationNames.includes(name));
    const tracks = spec.tracks || (preferred ? [preferred] : []);
    tracks.forEach((name, index) => {
      if (!data.findAnimation(name)) throw new Error(`Missing animation ${name} for ${spec.id}`);
      animationState.setAnimation(index, name, true);
    });
    // A fixed point in the idle loop gives a repeatable, fully assembled pose.
    for (let frame = 0; frame < 30; frame += 1) {
      const delta = 1 / 60;
      animationState.update(delta);
      animationState.apply(skeleton);
      skeleton.update(delta);
      skeleton.updateWorldTransform(spine.Physics.update);
    }
    return { atlas, skeleton, tracks, selectedSkins };
}
