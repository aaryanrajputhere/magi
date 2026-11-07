export type Draft = {
  asset_id: string; format: string; title: string; subject: string | null; body: string;
  preheader?: string | null; sender_name?: string | null; byline?: string | null;
  run_id?: string;
  status?: 'completed' | 'needs_review';
  validation?: {status: 'passed' | 'failed' | 'incomplete'; findings: string[]};
  publication_readiness?: 'ready' | 'blocked';
  publication_details?: {destination_url?: string | null; byline?: string | null; publication_date?: string | null};
  dependency_records?: {id: string; kind: string; message: string; status: string}[];
  image?: { image_id: string; alt_text: string; reason: string } | null;
  image_note?: string | null;
};
