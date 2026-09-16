import { DIFFICULTY_LABEL } from '../utils/difficulty';

const DifficultyPicker = ({ value, onChange }) => (
  <div className="review-difficulty-picker" role="group" aria-label="체감 난이도">
    {Object.entries(DIFFICULTY_LABEL).map(([level, label]) => (
      <button
        key={level}
        type="button"
        className={value === level ? 'active' : ''}
        onClick={() => onChange(level)}
      >
        {label}
      </button>
    ))}
  </div>
);

export default DifficultyPicker;
