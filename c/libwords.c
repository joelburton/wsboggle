#include <search.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
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
    bool found;
    int len;
} BoardWord;

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
    void *legal;
    char **word_array;
    int num_words;
    int longest;
    int score;
    char *dice_simple;
    Arena arena;   /* backs BoardWord structs + word strings */
} Board;

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
    // (and our free_tree-on-retry) doesn't read uninitialized memory.
    b->num_words = 0;
    b->longest = 0;
    b->legal = NULL;
    b->word_array = NULL;
    b->dice_simple = NULL;
    arena_init(&b->arena, ARENA_BYTES);
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
    shuffle_array(b->set, b->height * b->width);
    b->dice_simple = malloc((b->height * b->width + 1) * sizeof(char));

    int i = 0;
    for (int y = 0; y < b->height; y++) {
        for (int x = 0; x < b->width; x++) {
            if (i == (b->height * b->width)) return;
            char orig_face = b->set[y * b->width + x][random() % NUM_FACES];
            short face = (unsigned char) orig_face;
            if (face >= '0' && face <= '9')
                face = MULTIFACE_DICE[face - '0'];
            b->dice[i] = face;
            b->dice_simple[i] = orig_face;
            i++;
        }
    }
    b->dice_simple[i] = '\0';
}
/** Compare board words using the actual word. */

static int boardwords_cmp(const void *a,
                          const void *b) {
    const BoardWord *aa = a;
    const BoardWord *bb = b;
    return strcmp(aa->word, bb->word);
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
    // Both the struct and (eventually) the string come from the
    // arena. On retry the whole arena resets; no per-word free()s.
    BoardWord *b_word = arena_alloc(&board->arena, sizeof(BoardWord));
    b_word->word = word;  // temporary: points to caller's stack buffer
    b_word->found = false;
    b_word->len = length;

    BoardWord **found = tsearch(
        b_word, (void **) &board->legal, boardwords_cmp);

    // if already in tree, the just-allocated BoardWord becomes
    // arena dead-weight until the next retry resets — small and
    // bounded, so we leave it rather than burn a tfind walk first.
    if (*found != b_word) return ADD_DUP;

    // Tentative totals — if either bust the budget, back the
    // insert out so the tree never holds a BoardWord whose
    // ->word points to caller-stack memory (which goes away as
    // soon as find_all_words returns and we retry).
    const int new_count = board->num_words + 1;
    const int new_score = board->score + board->score_counts[length];
    if (new_count > board->max_words || new_score > board->max_score) {
        tdelete(b_word, (void **) &board->legal, boardwords_cmp);
        return ADD_FAIL;
    }

    // Commit: stable copy of the word string into the arena +
    // budget update. The arena copy is byte-equal to the original,
    // so the tree's ordering invariant (string compare on ->word)
    // is preserved when we swap the pointer.
    b_word->word = arena_strdup(&board->arena, word, length);
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

    // Make a bitmask for this tile position
    const int_least64_t mask = 0x1 << (y * board->width + x);

    // If we've already used this tile, can't make word here
    if (used & mask) return true;

    // Find the DAWG-node for existing-DAWG-node plus this letter.
    const short sought = toupper(board->dice[y * board->width + x]);

    if (sought < 256) {
        while (i != 0 && DAWG_LETTER(dawg, i) != sought) i = DAWG_NEXT(dawg, i);

        // There are no words continuing with this letter
        if (i == 0) return true;

        // Either this is a word or the stem of a word. So update our 'word' to
        // include this letter.
        word[word_len++] = tolower((char) sought);
    } else {
        // special tile, like QU
        short t1 = sought >> 8;
        short t2 = sought & 0xFF;

        while (i != 0 && DAWG_LETTER(dawg, i) != t1) i = DAWG_NEXT(dawg, i);

        // There are no words continuing with this letter
        if (i == 0) return true;

        i = DAWG_CHILD(dawg, i);
        while (i != 0 && DAWG_LETTER(dawg, i) != t2) i = DAWG_NEXT(dawg, i);
        if (i == 0) return true;

        // Either this is a word or the stem of a word. So update our 'word' to
        // include this letter.
        word[word_len++] = tolower((char) t1);
        word[word_len++] = tolower((char) t2);
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

    char word[MAX_WORD_LEN + 1];

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


/* The tree-walk machinery is platform-split: glibc's twalk_r passes
 * a user-data pointer; BSD/macOS twalk doesn't, so the callback has
 * to read from a file-scope global. Single-threaded usage (the GIL
 * serializes our get_words calls) means the global is safe. */

struct CollectBws {
    BoardWord **bws;   // every BoardWord in the tree, in walk order
    int marker;
};

#if __linux__
static void collect_bws_cb(const void *n, const VISIT value, void *data) {
    if (value == leaf || value == postorder) {
        struct CollectBws *c = data;
        c->bws[c->marker++] = *(BoardWord **)n;
    }
}

static void collect_bws(Board *board, BoardWord **out) {
    struct CollectBws c = {out, 0};
    twalk_r(board->legal, collect_bws_cb, &c);
}
#else
static struct CollectBws *collect_bws_cur;
static void collect_bws_cb(const void *n, const VISIT value, int depth) {
    (void)depth;
    if (value == leaf || value == postorder) {
        collect_bws_cur->bws[collect_bws_cur->marker++] = *(BoardWord **)n;
    }
}

static void collect_bws(Board *board, BoardWord **out) {
    struct CollectBws c = {out, 0};
    collect_bws_cur = &c;
    twalk(board->legal, collect_bws_cb);
    collect_bws_cur = NULL;
}
#endif

/** Drain a board's legal-word tree.
 *
 * Used in two places with the same shape — collect every
 * BoardWord, ``tdelete`` each so tsearch releases its internal
 * RB-tree node. The BoardWord structs and word strings live in
 * the arena, so we don't ``free`` them here; the caller decides
 * whether to ``arena_reset`` after draining (find_all_words
 * does; bws_btree_to_array doesn't, because word_array's pointers
 * still reference the strings).
 *
 * The temp BoardWord** scratch is a system-malloc rather than an
 * arena alloc — find_all_words is about to reset the arena, so
 * arena-allocating the scratch would invalidate it mid-loop.
 */

static void drain_tree(Board *board) {
    if (board->legal == NULL || board->num_words == 0) {
        board->legal = NULL;
        return;
    }
    BoardWord **bws = malloc((size_t) board->num_words * sizeof(BoardWord *));
    collect_bws(board, bws);

    for (int i = 0; i < board->num_words; i++) {
        tdelete(bws[i], (void **) &board->legal, boardwords_cmp);
    }
    free(bws);
    board->legal = NULL;
}

/* Backwards-compatible alias for the one call site that still uses
 * the old name (the forward declaration up top). Behavior matches
 * "drain tree, then arena_reset" — which is what find_all_words
 * wants. */
static void free_tree(Board *board, bool unused) {
    (void) unused;
    drain_tree(board);
    arena_reset(&board->arena);
}

/** Populate ``board->word_array`` (a fresh system-malloc) with the
 *  arena-resident word strings, then drain the tree. Critically we
 *  do NOT reset the arena here — the word_array's pointers point
 *  into the arena, and Python needs to read them before
 *  free_words tears it down.
 */

void bws_btree_to_array(Board *board) {
    board->word_array = malloc(((size_t) board->num_words + 1) * sizeof(char *));
    BoardWord **bws = malloc((size_t) board->num_words * sizeof(BoardWord *));
    collect_bws(board, bws);

    for (int i = 0; i < board->num_words; i++) {
        BoardWord *bw = bws[i];
        // word_array borrows a pointer into the arena; Python
        // reads it before the arena is freed in free_words.
        board->word_array[i] = (char *) bw->word;
        tdelete(bw, (void **) &board->legal, boardwords_cmp);
    }
    board->word_array[board->num_words] = NULL;
    free(bws);
    board->legal = NULL;
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
