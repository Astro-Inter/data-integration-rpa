import { prepareUser, UserDataError, userFingerprint } from "./domain.ts";
import { FirebaseAuthClient, FirebaseServiceError } from "./firebase-auth.ts";
import { iterateLegacyBatches, SyncStateRepository, type SqlClient } from "./repositories.ts";
import { TargetIdentityError, TargetUserRepository } from "./target-user-repository.ts";

export type SyncSummary = {
  total: number;
  new: number;
  changed: number;
  unchanged: number;
  confirmed: number;
  validated: number;
  invalid: number;
  validation_errors: Record<string, number>;
  last_synced_at: string | null;
};

export type SyncOptions = {
  legacy: SqlClient;
  destination: SqlClient;
  firebase: FirebaseAuthClient;
  batchSize: number;
  dryRun: boolean;
};

export async function runUserSync(options: SyncOptions): Promise<SyncSummary> {
  if (!Number.isSafeInteger(options.batchSize) || options.batchSize <= 0) throw new Error("batch_size_invalido");
  if (options.destination.transactionActive && !options.destination.transactionActive()) {
    throw new Error("transacao_destino_obrigatoria");
  }
  const state = new SyncStateRepository(options.destination);
  const targetUsers = new TargetUserRepository(options.destination);
  const confirm = !options.dryRun;
  if (confirm) await state.lockSync();

  const summary: SyncSummary = {
    total: 0, new: 0, changed: 0, unchanged: 0, confirmed: 0, validated: 0,
    invalid: 0, validation_errors: {}, last_synced_at: await state.lastSyncedAt(),
  };
  for await (const batch of iterateLegacyBatches(options.legacy, options.batchSize)) {
    const hashes = await state.confirmedHashes(batch.map((user) => user.id_funcionario));
    for (const user of batch) {
      summary.total++;
      const fingerprint = await userFingerprint(user);
      const previous = hashes.get(user.id_funcionario);
      if (previous === fingerprint) {
        summary.unchanged++;
        continue;
      }
      if (previous === undefined) summary.new++;
      else summary.changed++;

      let prepared;
      try { prepared = prepareUser(user); }
      catch (error) {
        if (!(error instanceof UserDataError)) throw error;
        if (confirm) throw error;
        summary.invalid++;
        for (const field of error.fields) summary.validation_errors[field] = (summary.validation_errors[field] ?? 0) + 1;
        continue;
      }
      summary.validated++;
      if (!confirm) continue;

      try {
        const knownUid = await targetUsers.resolveIdentity(prepared);
        const result = await options.firebase.ensureUser(prepared, knownUid ?? undefined);
        if (!result.uid) throw new FirebaseServiceError("UID Firebase não confirmado.");
        await targetUsers.persist(prepared, result.uid);
        await state.confirmUser(user.id_funcionario, fingerprint, new Date().toISOString());
        summary.confirmed++;
      } catch (error) {
        if (error instanceof FirebaseServiceError || error instanceof TargetIdentityError || error instanceof UserDataError) throw error;
        // Driver and transport details may include identifiers or connection data.
        throw new Error("Falha na persistência da integração.");
      }
    }
  }
  if (confirm) {
    summary.last_synced_at = new Date().toISOString();
    await state.confirmRun(summary.last_synced_at);
  }
  return summary;
}
