import type { Asset } from "../types";

export function uploadAsset(file: Blob, onProgress?: (percent: number) => void): Promise<Asset> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();

    if (onProgress) {
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100));
      });
    }

    xhr.addEventListener("load", () => {
      if (xhr.status === 401) {
        window.dispatchEvent(new CustomEvent("auth:unauthorized"));
        reject(new Error("登录已过期，请重新登录"));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as Asset);
        } catch {
          reject(new Error("解析响应失败"));
        }
        return;
      }
      try {
        const payload = JSON.parse(xhr.responseText) as { detail?: string };
        if (xhr.status === 403 && payload.detail === "Password change required") {
          window.dispatchEvent(new CustomEvent("auth:password-change-required"));
        }
        reject(new Error(payload.detail || `${xhr.status} ${xhr.statusText}`));
      } catch {
        reject(new Error(`${xhr.status} ${xhr.statusText}`));
      }
    });

    xhr.addEventListener("error", () => reject(new Error("网络错误")));
    xhr.addEventListener("abort", () => reject(new Error("上传已取消")));
    xhr.open("POST", "/api/assets");
    xhr.send(form);
  });
}
