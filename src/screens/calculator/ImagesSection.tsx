import type { Sku } from '../../engine';
import { useStore } from '../../state/store';
import { PaperclipIcon } from '../../components/ui';
import f from './SpecForm.module.css';

const MAX_IMAGES = 6;

/** Bag images: up to six reference photos per spec. */
export function ImagesSection({ sku }: { sku: Sku }) {
  const { actions } = useStore();
  const images = sku.images || [];
  return (
    <div>
      <div className={f.imgHead}>
        <h3 className={f.imgTitle}>Bag images</h3>
        <div className={f.spacer} />
        <div className={f.imgCount}>
          {images.length} / {MAX_IMAGES}
        </div>
        {images.length < MAX_IMAGES && (
          <button type="button" className={f.attachBtn} onClick={() => actions.attachImages(sku.id)}>
            <PaperclipIcon size={13} />
            Attach image
          </button>
        )}
      </div>
      {images.length > 0 && (
        <div className={f.imgGrid}>
          {images.map((src, i) => (
            <div key={i} className={f.imgTile}>
              {src && <div role="img" aria-label="Bag image" className={f.imgFill} style={{ backgroundImage: "url('" + src + "')" }} />}
              <button type="button" className={f.imgDel} title="Remove image" aria-label="Remove image" onClick={() => actions.removeImage(sku.id, i)}>
                ×
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
