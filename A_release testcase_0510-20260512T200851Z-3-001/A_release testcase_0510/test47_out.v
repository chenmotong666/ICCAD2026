module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n20,
    n22,
    n21,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  output n20;
  output n22;
  output n21;
  output n23;

  wire \$and$testcase/test47/test47.v:9$4_Y , \$or$testcase/test47/test47.v:10$6_Y , \$xor$testcase/test47/test47.v:11$8_Y , ___xor_testcase_test47_test47_v_11_8_n1_0__, ___xor_testcase_test47_test47_v_11_8_n2_0__, ___xor_testcase_test47_test47_v_11_8_n3_0__, ___xor_testcase_test47_test47_v_15_12_n1_0__, ___xor_testcase_test47_test47_v_15_12_n2_0__, ___xor_testcase_test47_test47_v_15_12_n3_0__, ___xor_testcase_test47_test47_v_32_23_n1_0__, ___xor_testcase_test47_test47_v_32_23_n2_0__, ___xor_testcase_test47_test47_v_32_23_n3_0__, ___xor_testcase_test47_test47_v_8_3_n1_0__, ___xor_testcase_test47_test47_v_8_3_n2_0__, ___xor_testcase_test47_test47_v_8_3_n3_0__, n100, n101, n102, n103, n104, n105, n107, n108, n109, n110, n111, n114, n120, n121, n122, n123, n128, n129, n130, n140, n141, n142;

  and   \$and$testcase/test47/test47.v:13$10  (n107, n2, n3);
  and   \$and$testcase/test47/test47.v:16$13  (n109, n2, n3);
  and   \$and$testcase/test47/test47.v:17$14  (n110, n2, n3);
  and   \$and$testcase/test47/test47.v:27$18  (n120, n2, n3);
  and   \$and$testcase/test47/test47.v:5$1  (n100, n2, n3);
  and   \$and$testcase/test47/test47.v:28$19  (n121, n2, n4);
  not   \$not$testcase/test47/test47.v:18$28  (n111, n4);
  not   \$not$testcase/test47/test47.v:21$30  (n114, n4);
  and   \$and$testcase/test47/test47.v:29$20  (n122, n2, n5);
  and   \$and$testcase/test47/test47.v:30$21  (n123, n2, n6);
  and   \$and$testcase/test47/test47.v:33$24  (n140, n6, n7);
  or    \$or$testcase/test47/test47.v:14$11  (n108, n107, n5);
  or    \$or$testcase/test47/test47.v:6$2  (n101, n100, n4);
  or    \$or$testcase/test47/test47.v:31$22  (n128, n120, n121);
  or    \$or$testcase/test47/test47.v:23$15  (n22, n114, n100);
  nand  xor_nand_6 (___xor_testcase_test47_test47_v_32_23_n1_0__, n122, n123);
  or    \$or$testcase/test47/test47.v:34$25  (n141, n140, n8);
  nand  xor_nand_3 (___xor_testcase_test47_test47_v_15_12_n1_0__, n108, n6);
  not   \$not$testcase/test47/test47.v:7$27  (n102, n101);
  nand  xor_nand_7 (___xor_testcase_test47_test47_v_32_23_n2_0__, n122, ___xor_testcase_test47_test47_v_32_23_n1_0__);
  nand  xor_nand_8 (___xor_testcase_test47_test47_v_32_23_n3_0__, n123, ___xor_testcase_test47_test47_v_32_23_n1_0__);
  not   \$not$testcase/test47/test47.v:35$31  (n142, n141);
  nand  xor_nand_4 (___xor_testcase_test47_test47_v_15_12_n2_0__, n108, ___xor_testcase_test47_test47_v_15_12_n1_0__);
  nand  xor_nand_5 (___xor_testcase_test47_test47_v_15_12_n3_0__, n6, ___xor_testcase_test47_test47_v_15_12_n1_0__);
  nand  xor_nand_9 (___xor_testcase_test47_test47_v_8_3_n1_0__, n102, n5);
  nand  \$xor$testcase/test47/test47.v:32$23  (n129, ___xor_testcase_test47_test47_v_32_23_n2_0__, ___xor_testcase_test47_test47_v_32_23_n3_0__);
  nand  \$xor$testcase/test47/test47.v:15$12  (n21, ___xor_testcase_test47_test47_v_15_12_n2_0__, ___xor_testcase_test47_test47_v_15_12_n3_0__);
  nand  xor_nand_10 (___xor_testcase_test47_test47_v_8_3_n2_0__, n102, ___xor_testcase_test47_test47_v_8_3_n1_0__);
  nand  xor_nand_11 (___xor_testcase_test47_test47_v_8_3_n3_0__, n5, ___xor_testcase_test47_test47_v_8_3_n1_0__);
  and   \$and$testcase/test47/test47.v:37$26  (n130, n129, n128);
  nand  \$xor$testcase/test47/test47.v:8$3  (n103, ___xor_testcase_test47_test47_v_8_3_n2_0__, ___xor_testcase_test47_test47_v_8_3_n3_0__);
  and   \$and$testcase/test47/test47.v:9$4  (\$and$testcase/test47/test47.v:9$4_Y , n103, n6);
  not   \$not$testcase/test47/test47.v:9$5  (n104, \$and$testcase/test47/test47.v:9$4_Y );
  or    \$or$testcase/test47/test47.v:10$6  (\$or$testcase/test47/test47.v:10$6_Y , n104, n7);
  not   \$not$testcase/test47/test47.v:10$7  (n105, \$or$testcase/test47/test47.v:10$6_Y );
  nand  xor_nand_0 (___xor_testcase_test47_test47_v_11_8_n1_0__, n105, n8);
  nand  xor_nand_1 (___xor_testcase_test47_test47_v_11_8_n2_0__, n105, ___xor_testcase_test47_test47_v_11_8_n1_0__);
  nand  xor_nand_2 (___xor_testcase_test47_test47_v_11_8_n3_0__, n8, ___xor_testcase_test47_test47_v_11_8_n1_0__);
  nand  \$xor$testcase/test47/test47.v:11$8  (\$xor$testcase/test47/test47.v:11$8_Y , ___xor_testcase_test47_test47_v_11_8_n2_0__, ___xor_testcase_test47_test47_v_11_8_n3_0__);
  not   \$not$testcase/test47/test47.v:11$9  (n20, \$xor$testcase/test47/test47.v:11$8_Y );

  assign n23 = 1'b1;

endmodule
