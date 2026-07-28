#include <unity.h>

void setUp(void) {}
void tearDown(void) {}

void test_example() {
    TEST_ASSERT_TRUE(true);  // scaffold boot-proof
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_example);
    return UNITY_END();
}
