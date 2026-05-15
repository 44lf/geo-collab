const MAX_DIMENSION = 1920;
const QUALITY = 0.85;
const SKIP_THRESHOLD_BYTES = 300 * 1024; // 小于 300KB 不压缩
const COMPRESSIBLE_TYPES = new Set(["image/jpeg", "image/png"]);

export async function compressImage(file: File): Promise<File> {
  if (!COMPRESSIBLE_TYPES.has(file.type) || file.size < SKIP_THRESHOLD_BYTES) {
    return file;
  }

  if (typeof createImageBitmap !== "function" || typeof OffscreenCanvas === "undefined") {
    return file;
  }

  let bitmap: ImageBitmap | null = null;
  try {
    bitmap = await createImageBitmap(file);
    const { width, height } = bitmap;

    const scale = Math.min(1, MAX_DIMENSION / Math.max(width, height));
    const targetW = Math.round(width * scale);
    const targetH = Math.round(height * scale);

    const canvas = new OffscreenCanvas(targetW, targetH);
    const ctx = canvas.getContext("2d");
    if (!ctx) return file;
    ctx.drawImage(bitmap, 0, 0, targetW, targetH);

    // PNG 转 JPEG（截图、图标等体积可缩小 5-10x）
    const outType = file.type === "image/png" ? "image/jpeg" : file.type;
    const blob = await canvas.convertToBlob({ type: outType, quality: QUALITY });

    // 压缩后比原始更大时（极少见）直接用原文件
    if (blob.size >= file.size) return file;

    return new File([blob], file.name.replace(/\.png$/i, ".jpg"), { type: outType });
  } catch {
    return file;
  } finally {
    bitmap?.close();
  }
}
