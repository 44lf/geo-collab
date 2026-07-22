import { useCallback, useEffect, useRef, useState } from "react";
import { listXhsThemes, regenerateXhsThemePreviews, type XhsThemePreview } from "../../api/xhsThemes";
import { useToast } from "../../components/Toast";

export function XhsStyleGallery() {
  const { toast } = useToast();
  const [themes, setThemes] = useState<XhsThemePreview[]>([]);
  const [generating, setGenerating] = useState(false);
  const [loading, setLoading] = useState(false);
  const pollRef = useRef<number | null>(null);
  const pollStartRef = useRef<number>(0);

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const fetchState = useCallback(async () => {
    const state = await listXhsThemes();
    setThemes(state.themes);
    setGenerating(state.generating);
    return state;
  }, []);

  const startPoll = useCallback(() => {
    if (pollRef.current) return; // 已在轮询
    pollStartRef.current = Date.now();
    pollRef.current = window.setInterval(() => {
      void (async () => {
        try {
          const state = await fetchState();
          const done = !state.generating && state.themes.every((t) => t.cached);
          if (done || Date.now() - pollStartRef.current > 90000) stopPoll();
        } catch (e) {
          stopPoll();
          toast(e instanceof Error ? e.message : "轮询失败", "error");
        }
      })();
    }, 2000);
  }, [fetchState, stopPoll, toast]);

  useEffect(() => {
    setLoading(true);
    void fetchState()
      .then((state) => {
        // 服务端预热在跑 → 自动轮询点亮，首屏无需手点
        if (state.generating) startPoll();
      })
      .catch((e) => toast(e instanceof Error ? e.message : "加载失败", "error"))
      .finally(() => setLoading(false));
    return stopPoll;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅挂载时拉一次
  }, []);

  async function regenerate() {
    try {
      await regenerateXhsThemePreviews();
      setGenerating(true);
      startPoll();
    } catch (e) {
      toast(e instanceof Error ? e.message : "生成失败", "error");
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
          <button
            className="secondaryButton"
            type="button"
            disabled={loading}
            onClick={() => void fetchState()}
          >
            刷新
          </button>
          <button
            className="primaryButton"
            type="button"
            disabled={generating}
            onClick={() => void regenerate()}
          >
            {generating ? "生成中…" : "重新生成预览"}
          </button>
        </div>
      </header>

      {generating && (
        <div className="aiEmptyState">
          <p className="aiEmptyText">正在渲染各主题预览，稍候会自动点亮…</p>
        </div>
      )}
      {!generating && noneCached && (
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
