export default function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="border border-neutral-200 rounded-xl bg-white shadow-sm px-5 py-4">
      <p className="text-2xl font-semibold text-neutral-900">{value.toLocaleString()}</p>
      <p className="text-sm text-neutral-400 mt-1">{label}</p>
    </div>
  );
}
