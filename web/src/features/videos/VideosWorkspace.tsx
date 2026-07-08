import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Film } from "lucide-react";
import { listVideos } from "../../api/videos";
import type { VideoJobSummary } from "../../types";
import { useToast } from "../../components/Toast";

const PAGE = 24;
type Filter = "all" | "done" | "failed";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "done", label: "已完成" },
  { key: "failed", label: "失败" },
];

export function VideosWorkspace() {
  const [filter, setFilter] = useState<Filter>("all");
  const [items, setItems] = useState<VideoJobSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const navigate = useNavigate();
  const { toast: showToast } = useToast();

  async function fetchPage(reset: boolean, curLen: number) {
    setLoading(true);
    try {
      const res = await listVideos({
        status: filter === "all" ? undefined : filter,
        skip: reset ? 0 : curLen,
        limit: PAGE,
      });
      setTotal(res.total);
      setItems((prev) => (reset ? res.items : [...prev, ...res.items]));
      setLoaded(true);
    } catch (e) {
      showToast(e instanceof Error ? e.message : "加载视频失败", "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void fetchPage(true, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const hasMore = items.length < total;

  return (
    <div style={{ padding: 24 }}>
      <header style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20 }}>
        <Film size={22} />
        <h2 style={{ margin: 0, fontSize: 20 }}>视频库</h2>
        <span style={{ color: "#888", fontSize: 13 }}>共 {total} 个</span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              style={{
                padding: "6px 14px",
                borderRadius: 8,
                border: "1px solid #ddd",
                background: filter === f.key ? "#2563eb" : "#fff",
                color: filter === f.key ? "#fff" : "#333",
                cursor: "pointer",
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      </header>

      {loaded && items.length === 0 && !loading && (
        <p className="emptyText" style={{ padding: 24 }}>
          还没有生成的视频
        </p>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
          gap: 16,
        }}
      >
        {items.map((v) => (
          <VideoCard key={v.job_id} video={v} onOpenArticle={(id) => navigate(`/article/${id}`)} />
        ))}
      </div>

      <div style={{ textAlign: "center", marginTop: 20 }}>
        {loading && <span style={{ color: "#888" }}>加载中…</span>}
        {!loading && hasMore && (
          <button
            type="button"
            onClick={() => void fetchPage(false, items.length)}
            style={{ padding: "8px 20px", borderRadius: 8, border: "1px solid #ddd", background: "#fff", cursor: "pointer" }}
          >
            加载更多
          </button>
        )}
      </div>
    </div>
  );
}

function VideoCard({
  video,
  onOpenArticle,
}: {
  video: VideoJobSummary;
  onOpenArticle: (articleId: number) => void;
}) {
  const title = video.title || video.article_title || `视频 ${video.job_id.slice(0, 8)}`;
  return (
    <div style={{ border: "1px solid #eee", borderRadius: 12, overflow: "hidden", background: "#fff", display: "flex", flexDirection: "column" }}>
      {video.status === "done" && video.video_url ? (
        <video
          src={video.video_url}
          controls
          preload="metadata"
          style={{ width: "100%", aspectRatio: "9 / 16", objectFit: "cover", background: "#000" }}
        />
      ) : (
        <div
          style={{
            width: "100%",
            aspectRatio: "9 / 16",
            background: "#f6f6f6",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "#c0392b",
            fontSize: 13,
            padding: 12,
            textAlign: "center",
          }}
        >
          {video.status === "failed" ? `渲染失败：${video.error ?? "未知错误"}` : "产物缺失"}
        </div>
      )}
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ fontWeight: 600, fontSize: 14, lineHeight: 1.3 }}>{title}</div>
        <button
          type="button"
          onClick={() => onOpenArticle(video.article_id)}
          style={{ alignSelf: "flex-start", padding: 0, border: "none", background: "none", color: "#2563eb", cursor: "pointer", fontSize: 12 }}
        >
          {video.article_title ? `源文章：${video.article_title}` : `源文章 #${video.article_id}`}
        </button>
        {video.tags.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {video.tags.map((t) => (
              <span key={t} style={{ fontSize: 11, background: "#f0f0f0", borderRadius: 4, padding: "2px 6px", color: "#666" }}>
                {t}
              </span>
            ))}
          </div>
        )}
        <div style={{ fontSize: 11, color: "#999" }}>{new Date(video.created_at).toLocaleString("zh-CN")}</div>
        {video.status === "done" && (
          <div style={{ display: "flex", gap: 12 }}>
            {video.video_url && (
              <a href={video.video_url} download style={{ fontSize: 12, color: "#2563eb" }}>
                下载 mp4
              </a>
            )}
            {video.srt_url && (
              <a href={video.srt_url} download style={{ fontSize: 12, color: "#2563eb" }}>
                下载 srt
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
