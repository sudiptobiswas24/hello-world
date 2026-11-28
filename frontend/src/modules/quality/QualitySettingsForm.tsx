import { RecordScreen } from "../../views/RecordScreen";

/** The one quality policy that is a choice rather than a rule: whether whoever measured a batch may also concede it. */
export default function QualitySettingsForm() {
  return (
    <RecordScreen
      endpoint="/api/quality/quality-settings/"
      back="/quality/quality-settings"
      backLabel="Quality settings"
      newTitle="New quality settings"
      heading={() => "Quality settings"}
      permissions={{ change: "quality.change_qualitysettings" }}
      fields={[
        { key: "concessions_need_a_second_person", label: "Concessions need a second person", kind: "bool", initial: false, hint: "On, whoever measured a batch cannot also take it out of specification. Off suits a plant where the inspector and the manager are one person" },
      ]}
    />
  );
}
