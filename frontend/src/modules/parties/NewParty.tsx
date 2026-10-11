import { useRef, useState } from "react";
import { useNavigate } from "react-router";

import { useAct } from "../../api/hooks";
import { useAccess } from "../../auth/me";
import { ActionButton, DocHeader, Sheet } from "../../forms/Document";
import { FieldSection } from "../../forms/FieldSection";
import { useLeaveGuard } from "../../forms/useDraft";
import { useRefs, type FieldDef } from "../../views/RecordScreen";
import { ADDRESS_FIELDS, BANK_FIELDS, CONTACT_FIELDS, GST_FIELDS, MSME_TDS_FIELDS, PARTY_FIELDS } from "./partyFields";

import type { ApiError } from "../../api/client";

type Row = Record<string, unknown>;

/** A group of a party's terms; one that is a control names the permission that sets it. */
export interface TermSection { title: string; fields: FieldDef[]; control?: string }

export interface NewPartySpec {
  /** "customer", "vendor": the word on the page and its buttons. */
  noun: string;
  plural: string;
  base: string;
  endpoint: string;
  termsPermission: string;
  sections: TermSection[];
  /** Goods may go to another address than the bill's: a customer's. */
  shipping?: boolean;
  /** The account they are paid into, and their MSME and TDS standing: a vendor's. */
  paid?: boolean;
}

const filled = (row: Row) => Object.values(row).some((value) => value !== "" && value !== null && value !== undefined && value !== false);

/**
 * A new party in one form: who they are, where they are, whom to speak
 * to, their tax standing and their terms, made together or not at all.
 * Each section is offered to whoever may fill it in, a control only to
 * whoever may set it; the rest is left for the people who keep it, on
 * the party's page.
 */
export function NewParty({ noun, plural, base, endpoint, termsPermission, sections, shipping: mayShip, paid }: NewPartySpec) {
  const navigate = useNavigate();
  const { can } = useAccess();
  const act = useAct<{ id: number }>();
  const refs = useRefs([...PARTY_FIELDS, ...ADDRESS_FIELDS, ...MSME_TDS_FIELDS, ...sections.flatMap((section) => section.fields)]);
  const [party, setParty] = useState<Row>({ code: "", name: "" });
  const [billing, setBilling] = useState<Row>({});
  const [shipsElsewhere, setShipsElsewhere] = useState(false);
  const [shipping, setShipping] = useState<Row>({});
  const [contact, setContact] = useState<Row>({});
  const [bank, setBank] = useState<Row>({});
  const [tax, setTax] = useState<Row>({});
  const [terms, setTerms] = useState<Row>({});
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  // Read by the save handler after the request: state lags a render.
  const saving = useRef(false);
  // Once made, nothing here is unsaved: the form renders again before the
  // router moves on, and its boxes are still filled.
  const made = useRef(false);
  const unsaved = useRef(false);
  unsaved.current = !made.current && [party, billing, shipping, contact, bank, tax, terms].some(filled);
  useLeaveGuard(unsaved);

  const may = {
    address: can("core.add_address"), contact: can("core.add_contact"), bank: paid && can("core.add_partybankaccount"),
    tax: can("accounting.add_partytaxprofile"), terms: can(termsPermission),
  };
  const offered = sections.filter((section) => !section.control || can(section.control));
  const editor = (setter: React.Dispatch<React.SetStateAction<Row>>) => (key: string, next: unknown) =>
    setter((current) => ({ ...current, [key]: next }));
  const Noun = noun.charAt(0).toUpperCase() + noun.slice(1);

  const save = async () => {
    if (saving.current) return;
    saving.current = true;
    const addresses = [
      ...(may.address && filled(billing) ? [{ ...billing, address_type: "billing", is_primary: true }] : []),
      ...(may.address && shipsElsewhere && filled(shipping) ? [{ ...shipping, address_type: "shipping", is_primary: true }] : []),
    ];
    const body = {
      party,
      addresses,
      contacts: may.contact && filled(contact) ? [{ ...contact, is_primary: true }] : [],
      bank_accounts: may.bank && filled(bank) ? [{ ...bank, is_primary: true }] : [],
      tax: may.tax && filled(tax) ? tax : null,
      terms: may.terms && filled(terms) ? terms : null,
    };
    const outcome = await act.run("POST", endpoint, body, { done: `${Noun} added` });
    saving.current = false;
    if (outcome.ok) {
      made.current = true;
      unsaved.current = false;
      navigate(`${base}/${outcome.data.id}`, { replace: true });
    }
    else setErrors((outcome.error as ApiError).fields ?? {});
  };

  return (
    <article className="doc">
      <DocHeader back={base} backLabel={plural} title={`New ${noun}`}>
        <ActionButton primary pending={act.pending} onClick={() => void save()}>{`Add ${noun}`}</ActionButton>
      </DocHeader>
      <Sheet>
        <FieldSection title="Who they are" fields={PARTY_FIELDS} value={party} set={editor(setParty)} errors={errors}
          prefix="party" editable refs={refs} />
        {may.address && (
          <>
            <FieldSection title={shipsElsewhere ? "Billing address" : "Address"} fields={ADDRESS_FIELDS} value={billing}
              set={editor(setBilling)} errors={errors} prefix="addresses.0" editable refs={refs} />
            {mayShip && (
              <label className="check">
                <input type="checkbox" checked={shipsElsewhere} onChange={(e) => setShipsElsewhere(e.target.checked)} />
                Goods go to another address
              </label>
            )}
            {mayShip && shipsElsewhere && (
              <FieldSection title="Shipping address" fields={ADDRESS_FIELDS} value={shipping} set={editor(setShipping)}
                errors={errors} prefix={filled(billing) ? "addresses.1" : "addresses.0"} editable refs={refs} />
            )}
          </>
        )}
        {may.contact && (
          <FieldSection title="Whom to speak to" fields={CONTACT_FIELDS} value={contact} set={editor(setContact)}
            errors={errors} prefix="contacts.0" editable refs={refs} />
        )}
        {may.bank && (
          <FieldSection title="Where they are paid" fields={BANK_FIELDS} value={bank} set={editor(setBank)}
            errors={errors} prefix="bank_accounts.0" editable refs={refs} />
        )}
        {may.tax && (
          <FieldSection title="GST" fields={GST_FIELDS} value={tax} set={editor(setTax)} errors={errors} prefix="tax"
            editable refs={refs} />
        )}
        {may.tax && paid && (
          <FieldSection title="MSME and TDS" fields={MSME_TDS_FIELDS} value={tax} set={editor(setTax)} errors={errors}
            prefix="tax" editable refs={refs} />
        )}
        {may.terms && offered.map((section) => (
          <FieldSection key={section.title} title={`Terms: ${section.title.toLowerCase()}`} fields={section.fields}
            value={terms} set={editor(setTerms)} errors={errors} prefix="terms" editable refs={refs} />
        ))}
        {errors.non_field_errors && <p className="form-error" role="alert">{errors.non_field_errors.join(" ")}</p>}
      </Sheet>
    </article>
  );
}
