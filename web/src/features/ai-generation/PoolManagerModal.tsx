import type { QuestionPool } from "../../types";
import { QuestionPoolManagerModal } from "../pipelines/question-pools/QuestionPoolManagerModal";

export function PoolManagerModal({
  pools: _pools,
  onClose,
  onChanged,
}: {
  pools: QuestionPool[];
  onClose: () => void;
  onChanged: () => void;
}) {
  void _pools;
  return <QuestionPoolManagerModal open onClose={onClose} onChanged={onChanged} />;
}
