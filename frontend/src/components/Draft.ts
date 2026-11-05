export type Draft = {
  asset_id: string; format: string; title: string; subject: string | null; body: string;
  preheader?: string | null; sender_name?: string | null; byline?: string | null;
  run_id?: string;
  status?: 'completed' | 'needs_review';
  validation?: {status: 'passed' | 'failed'; findings: string[]};
  image?: { image_id: string; alt_text: string; reason: string } | null;
};
