import type { Seg } from "./GameLibraryWorkspace";

type Props = {
  seg: Seg;
  onSeg: (s: Seg) => void;
  mainCount: number;
  companionCount: number;
};

export function GameGroupSwitch({ seg, onSeg, mainCount, companionCount }: Props) {
  const items: { key: Seg; label: string; count: number }[] = [
    { key: "main", label: "主推游戏", count: mainCount },
    { key: "companion", label: "陪衬游戏", count: companionCount },
  ];
  return (
    <div className="glGroupSwitch">
      {items.map((it) => (
        <button
          key={it.key}
          type="button"
          className={`glSegBtn${seg === it.key ? " active" : ""}`}
          onClick={() => onSeg(it.key)}
        >
          {it.label}
          <span className="glSegCount">{it.count}</span>
        </button>
      ))}
    </div>
  );
}
