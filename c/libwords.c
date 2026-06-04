#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>

#define CHILD_BIT_SHIFT 10
#define EOW_BIT_MASK 0X00000200
#define EOL_BIT_MASK 0X00000100
#define LTR_BIT_MASK 0X000000FF

#define DAWG_LETTER(arr, i) ((arr)[i] & LTR_BIT_MASK)
#define DAWG_EOW(arr, i)    ((arr)[i] & EOW_BIT_MASK)
#define DAWG_NEXT(arr, i)  (((arr)[i] & EOL_BIT_MASK) ? 0 : (i) + 1)
#define DAWG_CHILD(arr, i)  ((arr)[i] >> CHILD_BIT_SHIFT)

char err_msg[1024];

#define FATAL2(m, m2) { \
sprintf(err_msg, "%s:%i: (%s) %s %s", __FILE__, __LINE__, __FUNCTION__, m, m2); \
perror(err_msg); \
exit(1); \
}


/****************************** ARENA *****************************/

/* Per-board bump allocator. Replaces ~2N malloc/free pairs per
 * board attempt (one BoardWord and one word-string per accepted
 * word) with a single upfront allocation plus an O(1) reset
 * between rejection-sampler retries.
 *
 * The arena lives inline on the Board and survives until Python
 * calls free_words on the (returned word_array, board_handle)
 * pair — the word_array's char* pointers reference strings that
 * sit *inside* the arena, so the buffer can't be released earlier.
 *
 * Allocations are aligned to 8 bytes (enough for any field in
 * BoardWord on 64-bit targets). Overflow is fatal — the arena is
 * sized generously enough that hitting the cap means a bug, not a
 * legitimate edge.
 */

typedef struct {
    char *buf;
    size_t size;
    size_t used;
} Arena;

#define ARENA_BYTES (256 * 1024)

static void arena_init(Arena *a, size_t size) {
    a->buf = malloc(size);
    if (a->buf == NULL) FATAL2("arena malloc failed", "");
    a->size = size;
    a->used = 0;
}

static void arena_free(Arena *a) {
    free(a->buf);
    a->buf = NULL;
    a->size = a->used = 0;
}

static void arena_reset(Arena *a) {
    a->used = 0;
}

static void *arena_alloc(Arena *a, size_t n) {
    const size_t aligned = (a->used + 7) & ~(size_t) 7;
    if (aligned + n > a->size) {
        FATAL2("arena exhausted (bump ARENA_BYTES)", "");
    }
    void *p = a->buf + aligned;
    a->used = aligned + n;
    return p;
}

/** strdup-equivalent that allocates the copy from the arena. */
static char *arena_strdup(Arena *a, const char *s, size_t len) {
    char *out = arena_alloc(a, len + 1);
    memcpy(out, s, len);
    out[len] = '\0';
    return out;
}

typedef struct {
    const char *word;
    int len;   /* index into score_counts; also string length */
} BoardWord;


/****************************** HASH TABLE ***************************/

/* Open-addressed table with linear probing, in place of tsearch's
 * RB tree. Same role: dedup accepted words during the DFS. Reasons
 * for the swap:
 *
 * - tsearch malloc'd an internal RB-tree node per insert and walked
 *   the tree on every tdelete. With ~100 inserts per attempt and
 *   ~90 attempts per board on the extreme profile, that was the
 *   dominant cost the arena allocator couldn't reach.
 *
 * - A generation counter lets us "clear" the table between retries
 *   by incrementing one int. No memset, no per-slot work.
 *
 * - Linear probing + FNV-1a is branchless inside the hot probe
 *   loop, and the whole table fits in L1 on every 4×4/5×5 board
 *   (at HT_SIZE = 4096, the table is 64 KB).
 *
 * The table has no removal path: ADD_FAIL in add_word doesn't
 * commit (the reserved slot stays empty), so no tombstones ever
 * appear and the probe loop stops at the first empty slot.
 */

#define HT_SIZE 4096
#define HT_MASK (HT_SIZE - 1)

typedef struct {
    BoardWord *bw;     /* NULL if the slot's never been used */
    uint32_t gen;      /* matches Board.ht_gen iff this slot is live */
} HtSlot;

/* FNV-1a 32-bit. Cheap, decent distribution on short ASCII words,
 * no malloc anywhere. */
static uint32_t fnv1a(const char *s, int len) {
    uint32_t h = 0x811C9DC5u;
    for (int i = 0; i < len; i++) {
        h ^= (unsigned char) s[i];
        h *= 0x01000193u;
    }
    return h;
}

// Maximum board size is 6x6
typedef short Dice[36];

// We only read the dawg on startup, and it's shared among all boards.
const int32_t *dawg;

/** Read the dictionary file.
 *
 * Reads DAWG into memory.
 *
 * @param path
 */

#if __linux__
#include <sys/mman.h>
void read_dawg(const char *path) {
    const int fd = open(path, O_RDONLY);
    if (fd < 0) FATAL2("Cannot open dict at", path);

    int32_t nelems;
    if (read(fd, &nelems, 4) < 4) FATAL2("Cannot get size of", path);

    int32_t *f = mmap(
        NULL,
        (size_t) nelems * 4,
        PROT_READ,
        MAP_PRIVATE,
        fd,
        0);
    if (f == MAP_FAILED) FATAL2("Cannot read dict at", path);

    // Skip over the first integer, which was the # of dawg items
    dawg = f + 1;
}
#else
void read_dawg(const char *path) {
    FILE *f = fopen(path, "rb");
    if (f == NULL) FATAL2("Cannot open dict at", path);
    int32_t nelems;
    if (fread(&nelems, 4, 1, f) != 1) FATAL2("Cannot get size of", path);
    fseek(f, 0, SEEK_END);
    size_t size = ftell(f);
    fseek(f, 0, SEEK_SET);
    int32_t *f2 = malloc(size);
    if (fread(f2, size, 1, f) != 1) FATAL2("Cannot read dict at", path);
    dawg = f2 + 1;
}
#endif


/****************************** BOARD *****************************/


typedef struct Board {
    char **set;
    Dice dice;
    const int *score_counts;
    int width;
    int height;
    int min_words;
    int max_words;
    int min_score;
    int max_score;
    int min_longest;
    int max_longest;
    int min_legal;
    char **word_array;
    int num_words;
    int longest;
    int score;
    char *dice_simple;
    Arena arena;        /* backs BoardWord structs + word strings */
    HtSlot ht[HT_SIZE]; /* dedup table; cleared via ht_gen bump */
    uint32_t ht_gen;
} Board;

/** Probe the table for ``word``. Returns the existing BoardWord
 *  if it's already in the table, otherwise NULL and writes the
 *  slot the caller should commit to (via ``ht_commit``). On NULL
 *  the slot is "reserved" only in the sense that it sits at the
 *  end of the probe chain — leaving it empty after a failed
 *  budget check is safe, because subsequent lookups for the same
 *  hash will also stop at the same empty slot. */
static BoardWord *ht_find_or_reserve(
    Board *b, const char *word, int length, HtSlot **slot_out)
{
    uint32_t i = fnv1a(word, length) & HT_MASK;
    while (1) {
        HtSlot *s = &b->ht[i];
        if (s->gen != b->ht_gen) {
            *slot_out = s;
            return NULL;
        }
        if (strcmp(s->bw->word, word) == 0) {
            return s->bw;
        }
        i = (i + 1) & HT_MASK;
    }
}

static void ht_commit(Board *b, HtSlot *slot, BoardWord *bw) {
    slot->bw = bw;
    slot->gen = b->ht_gen;
}

/** Empty the table in O(1). Existing slots stay in memory but
 *  their ``gen`` no longer matches, so probes treat them as
 *  uninitialized. */
static void ht_reset(Board *b) {
    b->ht_gen++;
}


Board* make_board(
    char **set,
    const int score_counts[],
    int width,
    int height,
    int min_words,
    int max_words,
    int min_score,
    int max_score,
    int min_longest,
    int max_longest,
    int min_legal
) {
    if (width * height > 64)
        FATAL2("Oops", "Board too big");

    Board *b = malloc(sizeof(Board));
    b->set = set;
    b->score_counts = score_counts;
    // b->dice
    b->width = width;
    b->height = height;
    b->min_words = min_words;
    b->max_words = max_words == -1 ? INT32_MAX : max_words;
    b->min_score = min_score;
    b->max_score = max_score == -1 ? INT32_MAX : max_score;
    b->min_longest = min_longest;
    b->max_longest = max_longest == -1 ? INT32_MAX : max_longest;
    b->min_legal = min_legal;
    b->score = 0;
    // Init the scratch fields so the first call to find_all_words
    // doesn't read uninitialized memory.
    b->num_words = 0;
    b->longest = 0;
    b->word_array = NULL;
    b->dice_simple = NULL;
    arena_init(&b->arena, ARENA_BYTES);
    // Zero the hash table once + start ht_gen at 1 so the initial
    // slots' (zero) gen never matches the live gen. Subsequent
    // retries only need to bump ht_gen, no memset.
    memset(b->ht, 0, sizeof(b->ht));
    b->ht_gen = 1;
    return b;
}


#define NUM_FACES 6

/** Shuffle order of dice.
 *
 * A fair shuffle using Fisher-Yates.
 */

static void shuffle_array(char *array[], const int n) {
    for (long i = 0; i < n - 1; i++) {
        const long j = i + random() % (n - i);
        char *temp = array[j];
        array[j] = array[i];
        array[i] = temp;
    }
}

const short MULTIFACE_DICE[] = {
    ('_' << 8) + '_',
    ('Q' << 8) + 'U',
    ('I' << 8) + 'N',
    ('T' << 8) + 'H',
    ('E' << 8) + 'R',
    ('H' << 8) + 'E',
};

void make_dice(Board *b) {
    const int n = b->height * b->width;
    shuffle_array(b->set, n);
    b->dice_simple = malloc((size_t) n + 1);

    for (int i = 0; i < n; i++) {
        const char orig_face = b->set[i][random() % NUM_FACES];
        short face = (unsigned char) orig_face;
        if (face >= '0' && face <= '9')
            face = MULTIFACE_DICE[face - '0'];
        b->dice[i] = face;
        b->dice_simple[i] = orig_face;
    }
    b->dice_simple[n] = '\0';
}
enum ADD_RESULT {
    ADD_ADDED,
    ADD_DUP,
    ADD_FAIL,
};

/** Add word to the tree of legal words.
 *
 * Returns ADD_ADDED if the word was new and fits the board's
 * budget, ADD_DUP if we've already accepted this word (no state
 * change), or ADD_FAIL if accepting this word would bust max_words
 * or max_score (state is rolled back before returning, so the
 * caller can retry on a fresh board without poisoning the tree).
 */

static enum ADD_RESULT add_word(
    Board *board, const char word[], const int length)
{
    HtSlot *slot;
    if (ht_find_or_reserve(board, word, length, &slot) != NULL) {
        return ADD_DUP;
    }

    // Tentative totals — if either bust the budget, we just don't
    // commit the slot. The probe stopped at this empty slot and
    // will keep stopping there for any future caller (which, on a
    // failed attempt, there isn't — find_all_words bails on
    // ADD_FAIL and the next retry bumps ht_gen).
    const int new_count = board->num_words + 1;
    const int new_score = board->score + board->score_counts[length];
    if (new_count > board->max_words || new_score > board->max_score) {
        return ADD_FAIL;
    }

    // Commit: stable arena copy of the word, BoardWord struct,
    // hash-table slot, budget update.
    BoardWord *b_word = arena_alloc(&board->arena, sizeof(BoardWord));
    b_word->word = arena_strdup(&board->arena, word, length);
    b_word->len = length;

    ht_commit(board, slot, b_word);
    board->num_words = new_count;
    board->score = new_score;
    if (length > board->longest) board->longest = length;
    return ADD_ADDED;
}

/** Find all words starting from this tile and DAWG-pointer.
 *
 * This is a recursive function -- it is given a tile (via y and x)
 * and a DAWG pointer of where it is in a current word (along with the word
 * and word_len for that word). For example, it might be given the tile at
 * (1,1) and a DAWG-pointer to the end letter of C->A->T. For this example,
 * word="CAT" and word_len=3. It would the note that "CAT" is a good word,
 * and the recurse to all the neighboring tiles.
 *
 * Since you can only use a given tile once per word, it keeps a bitmask of
 * used tile positions. If the tile at the given position is already used,
 * this returns without continuing searching.
 *
 * @param board      Board
 * @param i          Pointer to item in DAWG
 * @param word       Word that we're currently making
 * @param word_len   length of word we're currently making
 * @param y          y pos of tile
 * @param x          x pos of tile
 * @param used       bitmask of tile positions used
 *
 * Returns true/false -- this isn't about "did this find a word?", but about
 *   whether we've violated an invariant (too many words, too high a score,
 *   etc.)
 */

static bool find_words( // NOLINT(*-no-recursion)
        Board *board,
        unsigned int i,
        char *word,
        int word_len,
        const int y,
        const int x,
        int_least64_t used)
{
    // If not a legal tile, can't make word here
    if (y < 0 || y >= board->height || x < 0 || x >= board->width) return true;

    // Make a bitmask for this tile position. Cast 1 to the wider
    // type *before* shifting — a shift on the 32-bit literal would
    // be UB at any tile index ≥ 31 (i.e. anything on a 6×6 board).
    const int_least64_t mask = ((int_least64_t) 1) << (y * board->width + x);

    // If we've already used this tile, can't make word here
    if (used & mask) return true;

    // Look up the DAWG node for "current prefix + this tile's letter".
    // Dice characters and DAWG entries are both uppercase by
    // construction (the dice-set tables ship uppercase, and multiface
    // tiles are encoded as ('Q'<<8)|'U' etc.), so no toupper is
    // needed. Words are stored uppercase here too; the Python wrapper
    // does .lower() on the way out.
    const short sought = board->dice[y * board->width + x];

    if (sought < 256) {
        while (i != 0 && DAWG_LETTER(dawg, i) != sought) i = DAWG_NEXT(dawg, i);

        // There are no words continuing with this letter
        if (i == 0) return true;

        // Either this is a word or the stem of a word. So update our 'word' to
        // include this letter.
        word[word_len++] = (char) sought;
    } else {
        // special tile, like QU
        const short t1 = sought >> 8;
        const short t2 = sought & 0xFF;

        while (i != 0 && DAWG_LETTER(dawg, i) != t1) i = DAWG_NEXT(dawg, i);

        // There are no words continuing with this letter
        if (i == 0) return true;

        i = DAWG_CHILD(dawg, i);
        while (i != 0 && DAWG_LETTER(dawg, i) != t2) i = DAWG_NEXT(dawg, i);
        if (i == 0) return true;

        word[word_len++] = (char) t1;
        word[word_len++] = (char) t2;
    }

    // Mark this tile as used
    used |= mask;


    // Add this word to the found-words.
    if (DAWG_EOW(dawg, i) && word_len >= board->min_legal) {
	word[word_len] = '\0';
        if (add_word(board, word, word_len) == ADD_FAIL) return false;
    }

    // Check every direction H/V/D from here (will also re-check this tile, but
    // the can't-reuse-this-tile rule prevents it from actually succeeding)
    for (int di = -1; di < 2; di++) {
        for (int dj = -1; dj < 2; dj++) {
            if (!find_words(
                board,
                DAWG_CHILD(dawg, i),
                word,
                word_len,
                y + di,
                x + dj,
                used
            )) return false;
        }
    }
    return true;
}


#define MAX_WORD_LEN 16


// Forward declaration so find_all_words can call the tree-cleanup
// helper at the top of each retry. The implementation lives below
// next to bws_btree_to_array since they share the tree-walk plumbing.
static void free_tree(Board *board, bool free_strings);


/** Find all words on board. */

bool find_all_words(Board *b) {
    // Drain the previous retry's tree (if any) before resetting.
    // Without this the rejection sampler leaks one full word list
    // per failed attempt — at tight constraints that's hundreds of
    // discarded trees per generated board.
    free_tree(b, true);
    b->num_words = 0;
    b->longest = 0;
    b->score = 0;

    // +2 (not +1) so the null terminator at word[word_len] always
    // fits: a multiface tile lands at indices word_len, word_len+1,
    // and the EOW write follows at word_len+2.
    char word[MAX_WORD_LEN + 2];

    for (int y = 0; y < b->height; y++) {
        for (int x = 0; x < b->width; x++) {
            if (!find_words(b, 1, word, 0, y, x, 0x0)) return false;
        }
    }
//    printf("num_words %d  min_words %d\n", b->num_words, b->min_words);
    if (b->num_words < b->min_words) return false;

//    printf("score %d  min_score %d\n", b->score, b->min_score);
    if (b->score < b->min_score) return false;

//    printf("longest %d  min_long %d\n", b->longest, b->min_longest);
    if (b->longest < b->min_longest) return false;

    return true;
}
int fill_board(Board *board, int max_tries){
    int count = 0;
    while (count++ < max_tries) {
        // make_dice mallocs a fresh dice_simple every retry; release
        // the previous one so the rejection sampler doesn't leak one
        // board-string per failed attempt.
        if (board->dice_simple != NULL) {
            free(board->dice_simple);
            board->dice_simple = NULL;
        }
        make_dice(board);
        if (find_all_words(board)) break;
    }
    return count;
}


/** Reset the dedup table + arena between rejection-sampler retries.
 *
 * Both are O(1): ht_reset bumps a generation counter so live slots
 * stop matching, arena_reset rewinds a bump pointer. The
 * BoardWords + word strings they held simply stop existing.
 */
static void free_tree(Board *board, bool unused) {
    (void) unused;
    ht_reset(board);
    arena_reset(&board->arena);
}

/** Walk the hash buckets once, copying each live entry's
 *  arena-resident word pointer into a freshly malloc'd
 *  ``word_array``. Python reads through that array before
 *  ``free_words`` tears the arena down. */

void bws_btree_to_array(Board *board) {
    board->word_array = malloc(((size_t) board->num_words + 1) * sizeof(char *));
    int n = 0;
    for (int i = 0; i < HT_SIZE; i++) {
        HtSlot *s = &board->ht[i];
        if (s->gen == board->ht_gen) {
            board->word_array[n++] = (char *) s->bw->word;
        }
    }
    board->word_array[n] = NULL;
}

char **get_words(
    char *set[],
    int score_counts[],
    int width,
    int height,
    int min_words,
    int max_words,
    int min_score,
    int max_score,
    int min_longest,
    int max_longest,
    int min_legal,
    int max_tries,
    int random_seed,
    int *num_tries,
    char **dice_simple,
    void **board_handle
) {
    srandom(random_seed);
    Board *b = make_board(
        set,
        score_counts,
        width,
        height,
        min_words,
        max_words,
        min_score,
        max_score,
        min_longest,
        max_longest,
        min_legal
    );

    *num_tries = fill_board(b, max_tries);
    *dice_simple = b->dice_simple;
    bws_btree_to_array(b);
    // word_array's char* pointers reference strings *inside* the
    // arena, which lives on Board. Python must hold the Board
    // handle until it's done reading the words; free_words then
    // tears the arena (and the Board) down.
    *board_handle = b;
    return b->word_array;
}


/** Release the (word_array, dice_simple, board_handle) triple
 *  returned by ``get_words``. NULL on any argument is allowed so
 *  the Python wrapper can call this unconditionally in a finally
 *  block. */

void free_words(char **words, char *dice_simple, void *board_handle) {
    Board *b = (Board *) board_handle;
    if (b != NULL) {
        // Word strings live in the arena; this is what releases
        // them. Don't iterate `words` freeing entries — they're
        // arena slabs, not malloc'd.
        arena_free(&b->arena);
        free(b);
    }
    if (words != NULL) free(words);
    if (dice_simple != NULL) free(dice_simple);
}
