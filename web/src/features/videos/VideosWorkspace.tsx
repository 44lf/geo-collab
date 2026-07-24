import { useEffect, useRef, useState } from "react";
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
  const seqRef = useRef(0);

  async function fetchPage(reset: boolean, curLen: number) {
    const seq = ++seqRef.current;
    setLoading(true);
    try {
      const res = await listVideos({
        status: filter === "all" ? undefined : filter,
        skip: reset ? 0 : curLen,
        limit: PAGE,
      });
      if (seq !== seqRef.current) return; // 更新已被更晚的请求取代，丢弃过期响应
      setTotal(res.total);
      setItems((prev) => (reset ? res.items : [...prev, ...res.items]));
      setLoaded(true);
    } catch (e) {
      if (seq !== seqRef.current) return;
      showToast(e instanceof Error ? e.message : "加载视频失败", "error");
    } finally {
      if (seq === seqRef.current) setLoading(false);
    }
  }

  useEffect(() => {
    void fetchPage(true, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const hasMore = items.length < total;
  const initialLoading = loading && items.length === 0;

  return (
    <div className="videoLibrary">
      <div className="topbar">
        <div>
          <p className="eyebrow">素材</p>
          <h1>视频库</h1>
        </div>
      </div>

      <div className="reviewTabs">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={`reviewTabBtn${filter === f.key ? " active" : ""}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
        <span className="videoLibraryCount">共 {total} 个</span>
      </div>

      <div className="videoLibraryGrid">
        {initialLoading &&
          Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="videoLibraryCardSkeleton" />
          ))}

        {!initialLoading && loaded && items.length === 0 && (
          <div className="videoLibraryEmptyState">
            <Film size={40} strokeWidth={1.2} />
            <p className="videoLibraryEmptyTitle">还没有生成的视频</p>
            <p>用视频生成 loop 产出配套视频后，会在这里展示</p>
          </div>
        )}

        {items.map((v) => (
          <VideoCard key={v.job_id} video={v} onOpenArticle={(id) => navigate(`/article/${id}`)} />
        ))}

        {(hasMore || (loading && items.length > 0)) && (
          <div className="videoLibraryFooter">
            {loading ? (
              <span className="videoLibraryLoadingText">加载中…</span>
            ) : (
              <button
                type="button"
                className="secondaryButton"
                onClick={() => void fetchPage(false, items.length)}
              >
                加载更多
              </button>
            )}
          </div>
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
  const fileBase = title.replace(/[\\/:*?"<>|]/g, "_");
  const playable = video.status === "done" && !!video.video_url;

  return (
    <div className="videoLibraryCard">
      <div className="videoLibraryCardMedia">
        {playable ? (
          <video src={video.video_url ?? undefined} controls preload="metadata" />
        ) : (
          <div className={`videoLibraryCardPlaceholder${video.status === "failed" ? " isFailed" : ""}`}>
            {video.status === "failed" ? `渲染失败：${video.error ?? "未知错误"}` : "产物缺失"}
          </div>
        )}
        <span className={`videoLibraryBadge videoLibraryBadge--${video.status === "done" ? "done" : "failed"}`}>
          {video.status === "done" ? "已完成" : "失败"}
        </span>
      </div>

      <div className="videoLibraryCardInfo">
        <p className="videoLibraryCardTitle" title={title}>
          {title}
        </p>
        <button
          type="button"
          className="videoLibraryCardArticle"
          onClick={() => onOpenArticle(video.article_id)}
        >
          {video.article_title ? `源文章：${video.article_title}` : `源文章 #${video.article_id}`}
        </button>
        {video.tags.length > 0 && (
          <div className="videoLibraryCardTags">
            {video.tags.map((t, i) => (
              <span key={`${t}-${i}`} className="videoLibraryTag">
                {t}
              </span>
            ))}
          </div>
        )}
        <div className="videoLibraryCardMeta">{new Date(video.created_at).toLocaleString("zh-CN")}</div>
        {video.status === "done" && (video.video_url || video.srt_url) && (
          <div className="videoLibraryCardDownloads">
            {video.video_url && (
              <a href={video.video_url} download={`${fileBase}.mp4`}>
                下载 mp4
              </a>
            )}
            {video.srt_url && (
              <a href={video.srt_url} download={`${fileBase}.srt`}>
                下载 srt
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
