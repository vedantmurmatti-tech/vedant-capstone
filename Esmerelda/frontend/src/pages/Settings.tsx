import { Construction, Mail, User } from "lucide-react";
import { useAsync } from "@/lib/useAsync";
import { getStudentName } from "@/lib/api";

export default function Settings() {
  const { data: name } = useAsync(getStudentName, []);

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h2 className="font-display text-xl font-semibold tracking-tight text-warm-50">Settings</h2>
        <p className="mt-1 text-sm text-graphite-400">Your Esmerelda profile and preferences.</p>
      </div>

      <section className="rounded-2xl border border-graphite-700/60 bg-graphite-850/40 p-5">
        <h3 className="mb-4 text-xs font-medium uppercase tracking-wide text-graphite-500">Profile</h3>
        <div className="space-y-3">
          <div className="flex items-center gap-3 border-b border-graphite-700/50 pb-3">
            <User size={15} className="text-graphite-500" />
            <span className="text-sm text-graphite-300">Name</span>
            <span className="ml-auto text-sm text-warm-50">{name ?? "—"}</span>
          </div>
          <div className="flex items-center gap-3">
            <Mail size={15} className="text-graphite-500" />
            <span className="text-sm text-graphite-300">Email</span>
            <span className="ml-auto truncate text-sm text-warm-50">student.design1@flame.edu.in</span>
          </div>
        </div>
      </section>

      <section className="flex items-start gap-3 rounded-2xl border border-graphite-700/60 bg-graphite-850/40 p-5">
        <Construction size={18} className="mt-0.5 shrink-0 text-graphite-500" />
        <div>
          <h3 className="text-sm font-medium text-warm-50">More preferences are on the way</h3>
          <p className="mt-1 text-sm text-graphite-400">
            Notification controls, sync frequency, and voice-input configuration will land here once the backend
            supports them.
          </p>
        </div>
      </section>
    </div>
  );
}
