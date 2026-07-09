import { api } from "./core";

export interface Skill {
  id: number;
  name: string;
  slug: string;
  is_official: boolean;
  current_version_label: string | null;
  file_count: number;
  total_bytes: number;
  updated_at: string;
  uploaded_by: number | null;
  category: string;
}

export interface SkillVersion {
  id: number;
  version_label: string;
  bundle_sha256: string;
  file_count: number;
  total_bytes: number;
  uploaded_by: number | null;
  uploaded_at: string;
  is_current: boolean;
}

export function listSkills(): Promise<{ skills: Skill[] }> {
  return api<{ skills: Skill[] }>("/api/mcp/skills");
}

export function uploadSkill(
  name: string,
  files: File[],
  category: string = "general",
): Promise<{ skill_id: number; slug: string; version_label: string }> {
  const form = new FormData();
  form.append("name", name);
  form.append("category", category);
  for (const file of files) {
    const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
    form.append("files", file, relativePath || file.name);
  }
  return api<{ skill_id: number; slug: string; version_label: string }>("/api/mcp/skills/upload", {
    method: "POST",
    body: form, // core.api 检测 FormData 自动免设 Content-Type
  });
}

export function uploadSkillVersion(
  skillId: number,
  files: File[],
): Promise<{ skill_id: number; slug: string; version_label: string }> {
  const form = new FormData();
  for (const file of files) {
    const relativePath = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
    form.append("files", file, relativePath || file.name);
  }
  return api<{ skill_id: number; slug: string; version_label: string }>(
    `/api/mcp/skills/${skillId}/versions`,
    { method: "POST", body: form },
  );
}

export function listSkillVersions(skillId: number): Promise<{ versions: SkillVersion[] }> {
  return api<{ versions: SkillVersion[] }>(`/api/mcp/skills/${skillId}/versions`);
}

export function setCurrentVersion(skillId: number, versionId: number): Promise<void> {
  return api<void>(`/api/mcp/skills/${skillId}/set-current`, {
    method: "POST",
    body: JSON.stringify({ version_id: versionId }),
  });
}

export function deleteSkillVersion(skillId: number, versionId: number): Promise<void> {
  return api<void>(`/api/mcp/skills/${skillId}/versions/${versionId}`, { method: "DELETE" });
}

export function deleteSkill(skillId: number): Promise<void> {
  return api<void>(`/api/mcp/skills/${skillId}`, { method: "DELETE" });
}

export function skillDownloadUrl(skillId: number): string {
  return `/api/mcp/skills/${skillId}/download.zip`;
}

export function skillInstallCommand(slug: string): string {
  return `在 Claude Code 里调用 install_loop_skills（当前官方包 slug=${slug}）`;
}
