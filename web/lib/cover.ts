// Playlist covers: Spotify wants a base64 JPEG of at most 256 KB.
// Uploaded images are centre-cropped to a square; generated covers are a gradient + the name.

export const COVER_SIZE = 640;
export const MAX_COVER_B64 = 256 * 1024;

/** Deterministic hue (0..359) from text, so a playlist name always gets the same colours. */
export function hueFromString(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h) % 360;
}

/** Split a title into at most `maxLines` lines of roughly `perLine` characters. */
export function wrapTitle(title: string, perLine = 14, maxLines = 4): string[] {
  const lines: string[] = [];
  let cur = "";
  for (const word of title.trim().split(/\s+/)) {
    const next = cur ? `${cur} ${word}` : word;
    if (next.length > perLine && cur) {
      lines.push(cur);
      cur = word;
    } else cur = next;
  }
  if (cur) lines.push(cur);
  if (lines.length > maxLines) {
    const kept = lines.slice(0, maxLines);
    kept[maxLines - 1] = kept[maxLines - 1].replace(/.{0,1}$/, "…");
    return kept;
  }
  return lines;
}

/** Base64 JPEG (no data: prefix) under Spotify's limit, lowering quality as needed. */
export function canvasToCoverJpeg(canvas: HTMLCanvasElement): string {
  for (const q of [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]) {
    const b64 = canvas.toDataURL("image/jpeg", q).split(",")[1];
    if (b64.length <= MAX_COVER_B64) return b64;
  }
  throw new Error("Couldn't compress the image under 256 KB");
}

function squareCanvas(): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = COVER_SIZE;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas not supported");
  return [canvas, ctx];
}

/** Uploaded image → centre-cropped square JPEG (base64). */
export async function coverFromFile(file: File): Promise<string> {
  if (!file.type.startsWith("image/")) throw new Error("Pick an image file");
  const bitmap = await createImageBitmap(file);
  const side = Math.min(bitmap.width, bitmap.height);
  const [canvas, ctx] = squareCanvas();
  ctx.drawImage(
    bitmap,
    (bitmap.width - side) / 2,
    (bitmap.height - side) / 2,
    side,
    side,
    0,
    0,
    COVER_SIZE,
    COVER_SIZE,
  );
  bitmap.close();
  return canvasToCoverJpeg(canvas);
}

/** Generated cover: two-colour gradient from the name, the name in big type, a small wordmark. */
export function generateCover(name: string, variant = 0): string {
  const [canvas, ctx] = squareCanvas();
  const hue = (hueFromString(name) + variant * 47) % 360;
  const g = ctx.createLinearGradient(0, 0, COVER_SIZE, COVER_SIZE);
  g.addColorStop(0, `hsl(${hue} 70% 45%)`);
  g.addColorStop(1, `hsl(${(hue + 70) % 360} 75% 18%)`);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, COVER_SIZE, COVER_SIZE);

  ctx.fillStyle = "rgba(255,255,255,0.95)";
  ctx.font = "bold 76px system-ui, -apple-system, Segoe UI, sans-serif";
  ctx.textBaseline = "top";
  wrapTitle(name || "Swave").forEach((line, i) => ctx.fillText(line, 48, 64 + i * 88));

  ctx.fillStyle = "rgba(255,255,255,0.7)";
  ctx.font = "600 30px system-ui, -apple-system, Segoe UI, sans-serif";
  ctx.textBaseline = "alphabetic";
  ctx.fillText("≋ Swave", 48, COVER_SIZE - 48);
  return canvasToCoverJpeg(canvas);
}
