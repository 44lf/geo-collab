import { useEffect, useRef, useState } from "react";
import { listXhsThemes, regenerateXhsThemePreviews, type XhsThemePreview } from "../../api/xhsThemes";
import { useToast } from "../../components/Toast";

export function XhsStyleGallery() {
  const { toast } = useToast();
  const [themes, setThemes] = useState<XhsThemePreview[]>([]);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const pollRef = useRef<number | null>(null);

  async function load() {
    setLoading(true);
    try {
      setThemes(await listXhsThemes());
    } catch (e) {
      toast(e instanceof Error ? e.message : "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅挂载时拉一次
  }, []);

  async function regenerate() {
    setGenerating(true);
    try {
      await regenerateXhsThemePreviews();
      const started = Date.now();
      pollRef.current = window.setInterval(() => {
        void (async () => {
          try {
            const rows = await listXhsThemes();
            setThemes(rows);
            if (rows.every((t) => t.cached) || Date.now() - started > 60000) {
              if (pollRef.current) window.clearInterval(pollRef.current);
              pollRef.current = null;
              setGenerating(false);
            }
          } catch (e) {
            if (pollRef.current) window.clearInterval(pollRef.current);
            pollRef.current = null;
            setGenerating(false);
            toast(e instanceof Error ? e.message : "轮询失败", "error");
          }
        })();
      }, 2000);
    } catch (e) {
      toast(e instanceof Error ? e.message : "生成失败", "error");
      setGenerating(false);
    }
  }

  const noneCached = themes.length > 0 && themes.every((t) => !t.cached);

  return (
    <div className="promptsWorkspace">
      <header className="topbar">
        <div>
          <p className="eyebrow">内容资产</p>
          <h1>小红书样式库</h1>
        </div>
        <div className="topActions">
          <button className="secondaryButton" type="button" disabled={loading} onClick={() => void load()}>
            刷新
          </button>
          <button className="primaryButton" type="button" disabled={generating} onClick={() => void regenerate()}>
            {generating ? "生成中…" : "重新生成预览"}
          </button>
        </div>
      </header>

      {noneCached && !generating && (
        <div className="aiEmptyState">
          <p className="aiEmptyText">预览还没生成，点「重新生成预览」渲染各主题样卡</p>
        </div>
      )}

      <div className="promptTemplateList">
        {themes.map((t) => (
          <article key={t.name} className="promptTemplateCard">
            <div className="promptTemplateHeader">
              <div>
                <div className="aiCardName">{t.label}</div>
                <div className="promptTemplateMeta">
                  <span className="badge">{t.name}</span>
                  <span className={`badge ${t.cached ? "succeeded" : "pending"}`}>
                    {t.cached ? "已生成" : "待生成"}
                  </span>
                </div>
              </div>
            </div>
            {t.cached ? (
              <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                <img
                  src={t.cover_url}
                  alt={`${t.label} 封面`}
                  loading="lazy"
                  style={{ width: "50%", borderRadius: "var(--r)", objectFit: "cover" }}
                />
                <img
                  src={t.card_url}
                  alt={`${t.label} 正文卡`}
                  loading="lazy"
                  style={{ width: "50%", borderRadius: "var(--r)", objectFit: "cover" }}
                />
              </div>
            ) : (
              <div className="aiEmptyText" style={{ padding: "24px 0" }}>
                未生成
              </div>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}
